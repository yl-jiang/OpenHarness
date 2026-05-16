"""Gateway bridge for the standalone self-log app."""

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
from self_log.store import SelfLogStore
from self_log.tools import SelfLogToolAgent, SelfLogToolRegistry

logger = get_logger(__name__)


def _content_snippet(text: str, *, limit: int = 160) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


class SelfLogGatewayBridge:
    """Consume inbound channel messages and execute only self-log actions."""

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

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                message = await asyncio.wait_for(self._bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            await self._handle_message(message)

    def stop(self) -> None:
        self._running = False

    async def _handle_message(self, message: InboundMessage) -> None:
        content = message.content.strip()
        if not content:
            return
        logger.info(
            "self-log inbound received channel=%s chat_id=%s sender_id=%s session_key=%s content=%r",
            message.channel,
            message.chat_id,
            message.sender_id,
            message.session_key,
            _content_snippet(content),
        )
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
        except Exception as exc:
            logger.exception("self-log gateway failed channel=%s chat_id=%s", message.channel, message.chat_id)
            reply = f"self-log 执行失败：{exc}"
        await self._publish_reply(message, reply)

    async def _handle_record(self, message: InboundMessage, store: SelfLogStore, content: str) -> str:
        agent = SelfLogToolAgent(
            SelfLogToolRegistry(store),
            OpenHarnessSelfLogAgent(profile=self._provider_profile),
        )
        return await agent.run(content)

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
