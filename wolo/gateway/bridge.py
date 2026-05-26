"""Gateway bridge connecting channel bus traffic to the wolo agent."""

from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path

from openharness.channels.bus.events import InboundMessage, OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.utils.log import get_logger

from wolo.agent import OpenHarnessWoloAgent
from wolo.commands import (
    format_process_result,
    parse_backfill_argument,
    parse_wolo_command,
    wolo_help_text,
)
from wolo.processor import WoloProcessor
from wolo.runner import WoloQueryRunner
from wolo.core.store import WoloStore

logger = get_logger(__name__)

_CONTENT_DEDUP_WINDOW = 300  # seconds: ignore identical messages within this window


def _hash_content(text: str) -> str:
    normalized = " ".join(text.split())
    return hashlib.sha256(normalized.encode()).hexdigest()


def _content_snippet(text: str, *, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _source_message_id(message: InboundMessage) -> str | None:
    metadata = message.metadata or {}
    for key in ("message_id", "event_id"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def _build_source_context(message: InboundMessage) -> dict[str, object]:
    return {
        "channel": message.channel,
        "sender_id": message.sender_id,
        "chat_id": message.chat_id,
        "message_id": _source_message_id(message),
        "message_metadata": dict(message.metadata or {}),
        "media": list(message.media),
        "session_key": message.session_key,
        "received_at": message.timestamp.isoformat(),
    }


def _format_gateway_error(exc: Exception) -> str:
    """Return a short, user-facing error message."""
    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "claude oauth refresh failed" in lowered:
        return "Claude 订阅认证过期，请重新运行 `oh auth claude-login`。"
    if "claude oauth refresh token is invalid or expired" in lowered:
        return "Claude 订阅 token 已过期，请运行 `claude auth login` 后重新执行 `oh auth claude-login`。"
    if "auth source not found" in lowered or "access token" in lowered:
        return "认证未配置，请运行 `wolo config` 设置 provider。"
    if "api key" in lowered or "credential" in lowered or "auth" in lowered:
        return "认证失败，请检查 `oh auth status` 和 `wolo config`。"
    return f"wolo 执行失败：{message}"


class WoloGatewayBridge:
    """Consume inbound channel messages and execute wolo actions."""

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
        self._session_content_hashes: dict[str, str] = {}  # session_key -> hash of in-flight content
        self._recent_success_hashes: dict[str, tuple[str, float]] = {}  # session_key -> (hash, monotonic_ts)

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
                "wolo inbound received channel=%s chat_id=%s sender_id=%s session_key=%s content=%r",
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
            content_hash = _hash_content(content)

            # Dedup: same content is still running in this session
            running_task = self._session_tasks.get(session_key)
            if running_task is not None and not running_task.done():
                if self._session_content_hashes.get(session_key) == content_hash:
                    logger.info(
                        "wolo content dedup (running) channel=%s session_key=%s",
                        message.channel,
                        session_key,
                    )
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=message.channel,
                            chat_id=message.chat_id,
                            content="⏳ 这条消息正在处理中，请稍候。",
                            metadata={"_session_key": session_key},
                        )
                    )
                    continue

            # Dedup: same content was successfully processed within the window
            entry = self._recent_success_hashes.get(session_key)
            if entry is not None:
                stored_hash, stored_ts = entry
                if stored_hash == content_hash and time.monotonic() - stored_ts < _CONTENT_DEDUP_WINDOW:
                    logger.info(
                        "wolo content dedup (recent) channel=%s session_key=%s",
                        message.channel,
                        session_key,
                    )
                    await self._bus.publish_outbound(
                        OutboundMessage(
                            channel=message.channel,
                            chat_id=message.chat_id,
                            content="✅ 这条消息刚才已经处理完成了，无需重复提交。",
                            metadata={"_session_key": session_key},
                        )
                    )
                    continue

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
            self._session_content_hashes[session_key] = content_hash
            task = asyncio.create_task(
                self._process_message(message),
                name=f"wolo-session:{session_key}",
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
        content_hash = _hash_content(content)
        command = parse_wolo_command(content)
        store = WoloStore(self._workspace)
        _succeeded = False
        try:
            if command is None:
                reply = await self._handle_record(message, store, content)
            elif command.action == "help":
                reply = wolo_help_text()
            elif command.action == "process":
                result = await WoloProcessor(
                    store,
                    OpenHarnessWoloAgent(profile=self._provider_profile),
                ).process_pending(backfill_missing_yesterday=True)
                reply = format_process_result(result)
            elif command.action == "status":
                reply = _status_wolo(store)
            elif command.action == "view":
                reply = _view_wolo(store, command.limit)
            elif command.action == "report":
                processor = WoloProcessor(
                    store,
                    OpenHarnessWoloAgent(profile=self._provider_profile),
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
            _succeeded = True
        except asyncio.CancelledError:
            logger.info(
                "wolo session interrupted channel=%s chat_id=%s session_key=%s reason=%s",
                message.channel,
                message.chat_id,
                message.session_key,
                self._session_cancel_reasons.get(message.session_key, "cancelled"),
            )
            raise
        except Exception as exc:
            logger.exception(
                "wolo gateway failed channel=%s chat_id=%s", message.channel, message.chat_id
            )
            reply = _format_gateway_error(exc)
        finally:
            self._session_content_hashes.pop(message.session_key, None)
        if _succeeded:
            self._recent_success_hashes[message.session_key] = (content_hash, time.monotonic())
        await self._publish_reply(message, reply)

    async def _handle_record(self, message: InboundMessage, store: WoloStore, content: str) -> str:
        runner = WoloQueryRunner(store, profile=self._provider_profile)
        async for kind, text in runner.stream_run(
            content,
            session_key=message.session_key,
            media=message.media,
            source_context=_build_source_context(message),
        ):
            if kind == "final":
                return text
            if kind not in {"progress", "tool_hint"}:
                continue
            if text:
                await self._bus.publish_outbound(
                    OutboundMessage(
                        channel=message.channel,
                        chat_id=message.chat_id,
                        content=text,
                        metadata={
                            "_progress": True,
                            "_tool_hint": kind == "tool_hint",
                            "_session_key": message.session_key,
                        },
                    )
                )
        return ""

    async def _handle_backfill(
        self,
        store: WoloStore,
        content: str,
        date: str | None,
    ) -> str:
        if not content:
            return "请提供要补录的内容。"
        backfill_date = date or parse_backfill_argument(content)[0]
        entry = store.record(content, metadata={"record_date": backfill_date, "source": "补录"})
        result = await WoloProcessor(
            store,
            OpenHarnessWoloAgent(profile=self._provider_profile),
        ).process_pending(limit=20)
        return f"已补录 {backfill_date}。entry_id={entry.id}\n{format_process_result(result)}"

    async def _publish_reply(self, message: InboundMessage, content: str) -> None:
        logger.info(
            "wolo outbound final channel=%s chat_id=%s session_key=%s content=%r",
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


def _view_wolo(store: WoloStore, limit: int) -> str:
    records = store.list_records(limit=limit)
    if not records:
        return "暂无已整理 wolo 记录。"
    return "\n".join(
        f"{record.date} {record.emotion} [{record.source}] [{record.tags}] {record.summary}"
        for record in records
    )


def _status_wolo(store: WoloStore) -> str:
    status = store.status()
    return (
        f"wolo 状态：entries={status['entries']} "
        f"records={status['records']} todos={status['todos']} "
        f"decisions={status['decisions']} highlights={status['highlights']} "
        f"pending={status['pending_confirmations']} "
        f"path={status['path']}"
    )
