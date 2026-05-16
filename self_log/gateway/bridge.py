"""Gateway bridge connecting channel bus traffic to the self-log agent."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.utils.log import get_logger

from self_log.agent import OpenHarnessSelfLogAgent
from self_log.commands import (
    format_process_result,
    parse_backfill_argument,
    parse_self_log_command,
    self_log_help_text,
)
from self_log.processor import SelfLogProcessor
from self_log.runner import SelfLogQueryRunner
from self_log.store import SelfLogStore

logger = get_logger(__name__)


def _content_snippet(text: str, *, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _format_gateway_error(exc: Exception) -> str:
    """Return a short, user-facing error message."""
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "claude oauth refresh failed" in lowered:
        return "Claude 订阅认证过期，请重新运行 `oh auth claude-login`。"
    if "claude oauth refresh token is invalid or expired" in lowered:
        return "Claude 订阅 token 已过期，请运行 `claude auth login` 后重新执行 `oh auth claude-login`。"
    if "auth source not found" in lowered or "access token" in lowered:
        return "认证未配置，请运行 `self-log config` 设置 provider。"
    if "api key" in lowered or "credential" in lowered or "auth" in lowered:
        return "认证失败，请检查 `oh auth status` 和 `self-log config`。"
    return f"self-log 执行失败：{message}"


class SelfLogGatewayBridge:
    """Consume inbound channel messages and execute self-log actions."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        workspace: str | Path | None = None,
        provider_profile: str | None = None,
    ) -> None:
        self._bus = bus
        self._workspace = workspace
        self._provider_profile = provider_profile
        self._running = False
        self._session_tasks: dict[str, asyncio.Task[None]] = {}
        self._session_cancel_reasons: dict[str, str] = {}

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                message = await asyncio.wait_for(self._bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            content = message.content.strip()
            if not content:
                continue

            logger.info(
                "self-log inbound received channel=%s chat_id=%s sender_id=%s session_key=%s content=%r",
                message.channel,
                message.chat_id,
                message.sender_id,
                message.session_key,
                _content_snippet(content),
            )

            if content == "/stop":
                await self._handle_stop(message)
                continue

            session_key = message.session_key
            await self._interrupt_session(
                session_key,
                reason="replaced by a newer user message",
                notify=OutboundMessage(
                    channel=message.channel,
                    chat_id=message.chat_id,
                    content="⏹️ 已停止上一条正在处理的任务，继续看你的最新消息。",
                    metadata={"_progress": True, "_session_key": session_key},
                ),
            )
            task = asyncio.create_task(
                self._process_message(message),
                name=f"self-log-session:{session_key}",
            )
            self._session_tasks[session_key] = task
            task.add_done_callback(lambda t, key=session_key: self._cleanup_task(key, t))

    def stop(self) -> None:
        self._running = False
        for session_key, task in list(self._session_tasks.items()):
            self._session_cancel_reasons[session_key] = "gateway stopping"
            task.cancel()

    async def _handle_stop(self, message: InboundMessage) -> None:
        session_key = message.session_key
        stopped = await self._interrupt_session(session_key, reason="stopped by user command")
        content = "⏹️ 已停止当前正在运行的任务。" if stopped else "当前没有正在运行的任务。"
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=message.channel,
                chat_id=message.chat_id,
                content=content,
                metadata={"_session_key": session_key},
            )
        )

    async def _interrupt_session(
        self,
        session_key: str,
        *,
        reason: str = "replaced by newer message",
        notify: OutboundMessage | None = None,
    ) -> bool:
        task = self._session_tasks.get(session_key)
        if task is None or task.done():
            return False
        self._session_cancel_reasons[session_key] = reason
        task.cancel()
        if notify is not None:
            await self._bus.publish_outbound(notify)
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        return True

    def _cleanup_task(self, session_key: str, task: asyncio.Task[None]) -> None:
        current = self._session_tasks.get(session_key)
        if current is task:
            self._session_tasks.pop(session_key, None)
        self._session_cancel_reasons.pop(session_key, None)

    async def _process_message(self, message: InboundMessage) -> None:
        content = message.content.strip()
        command = parse_self_log_command(content)
        store = SelfLogStore(self._workspace)
        try:
            if command is None:
                reply = await self._handle_record(message, store, content)
            elif command.action == "help":
                reply = self_log_help_text()
            elif command.action == "process":
                result = await SelfLogProcessor(
                    store,
                    OpenHarnessSelfLogAgent(profile=self._provider_profile),
                ).process_pending(backfill_missing_yesterday=True)
                reply = format_process_result(result)
            elif command.action == "status":
                reply = _status_self_log(store)
            elif command.action == "view":
                reply = _view_self_log(store, command.limit)
            elif command.action == "report":
                processor = SelfLogProcessor(
                    store,
                    OpenHarnessSelfLogAgent(profile=self._provider_profile),
                )
                process_result = await processor.process_pending()
                report = await processor.generate_report(command.report_type)
                prefix = (
                    f"已先整理 {process_result.auto_processed} 条新记录。\n\n"
                    if process_result.auto_processed
                    else ""
                )
                reply = prefix + report.content
            elif command.action == "backfill":
                reply = await self._handle_backfill(store, command.content, command.backfill_date)
            else:
                reply = await self._handle_record(message, store, content)
        except asyncio.CancelledError:
            logger.info(
                "self-log session interrupted channel=%s chat_id=%s session_key=%s reason=%s",
                message.channel,
                message.chat_id,
                message.session_key,
                self._session_cancel_reasons.get(message.session_key, "cancelled"),
            )
            raise
        except Exception as exc:
            logger.exception(
                "self-log gateway failed channel=%s chat_id=%s", message.channel, message.chat_id
            )
            reply = _format_gateway_error(exc)
        await self._publish_reply(message, reply)

    async def _handle_record(self, message: InboundMessage, store: SelfLogStore, content: str) -> str:
        runner = SelfLogQueryRunner(store, profile=self._provider_profile)
        return await runner.run(content, session_key=message.session_key)

    async def _handle_backfill(
        self,
        store: SelfLogStore,
        content: str,
        date: str | None,
    ) -> str:
        if not content:
            return "请提供要补录的内容。"
        backfill_date = date or parse_backfill_argument(content)[0]
        entry = store.record(content, metadata={"record_date": backfill_date, "source": "补录"})
        result = await SelfLogProcessor(
            store,
            OpenHarnessSelfLogAgent(profile=self._provider_profile),
        ).process_pending(limit=20)
        return f"已补录 {backfill_date}。entry_id={entry.id}\n{format_process_result(result)}"

    async def _publish_reply(self, message: InboundMessage, content: str) -> None:
        logger.info(
            "self-log outbound final channel=%s chat_id=%s session_key=%s content=%r",
            message.channel,
            message.chat_id,
            message.session_key,
            _content_snippet(content),
        )
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=message.channel,
                chat_id=message.chat_id,
                content=content,
                metadata={"_session_key": message.session_key},
            )
        )


def _view_self_log(store: SelfLogStore, limit: int) -> str:
    records = store.list_records(limit=limit)
    if not records:
        return "暂无已整理 self-log 记录。"
    return "\n".join(
        f"{record.date} {record.emotion} [{record.source}] [{record.tags}] {record.summary}"
        for record in records
    )


def _status_self_log(store: SelfLogStore) -> str:
    status = store.status()
    return (
        f"self-log 状态：entries={status['entries']} "
        f"records={status['records']} pending={status['pending_confirmations']} "
        f"path={status['path']}"
    )

