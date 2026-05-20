"""App-local heartbeat for the standalone solo gateway."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.utils.log import get_logger

from solo.runner import SoloQueryRunner
from solo.session import list_conversations, load_conversation, save_conversation
from solo.store import SoloStore
from solo.workspace import get_workspace_root

logger = get_logger(__name__)


class _Runner(Protocol):
    async def run(self, user_text: str, session_key: str = "", **kwargs: object) -> str: ...


@dataclass(frozen=True)
class HeartbeatResult:
    executed: bool
    notified: bool = False
    reason: str = ""
    response: str = ""


class SoloHeartbeatService:
    """Periodically wakes solo to handle pending app-local work."""

    def __init__(
        self,
        *,
        bus: MessageBus,
        workspace: str | Path | None,
        provider_profile: str | None,
        enabled_channels: list[str],
        interval_s: int = 30 * 60,
        enabled: bool = True,
        keep_recent_messages: int = 8,
        runner_factory: type[_Runner] = SoloQueryRunner,
    ) -> None:
        self._bus = bus
        self._workspace = get_workspace_root(workspace)
        self._provider_profile = provider_profile
        self._enabled_channels = set(enabled_channels)
        self._interval_s = max(1, interval_s)
        self._enabled = enabled
        self._keep_recent_messages = max(0, keep_recent_messages)
        self._runner_factory = runner_factory
        self._running = False
        self._task: asyncio.Task[None] | None = None

    @property
    def heartbeat_file(self) -> Path:
        return self._workspace / "HEARTBEAT.md"

    async def start(self) -> None:
        if not self._enabled:
            logger.info("solo heartbeat disabled")
            return
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="solo-heartbeat")
        logger.info("solo heartbeat started interval_s=%d", self._interval_s)

    async def stop(self) -> None:
        self._running = False
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(self._interval_s)
                if self._running:
                    await self.trigger_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("solo heartbeat tick failed")

    def status(self) -> dict[str, object]:
        agenda = self.build_agenda()
        target = self._pick_notify_target()
        return {
            "enabled": self._enabled,
            "interval_s": self._interval_s,
            "agenda": agenda is not None,
            "notify_target": f"{target[0]}:{target[1]}" if target else "",
        }

    def build_agenda(self) -> str | None:
        store = SoloStore(self._workspace)
        sections: list[str] = []

        pending = store.list_pending_confirmations()[:5]
        if pending:
            lines = ["## 待确认记录"]
            for item in pending:
                question = item.questions[0] if item.questions else item.clarification_reason
                lines.append(f"- {item.raw_content}；需要确认：{question}")
            sections.append("\n".join(lines))

        todos = store.list_todos(status="pending", limit=10)
        if todos:
            lines = ["## Open Todos"]
            for todo in todos:
                due = f" due={todo.due_date}" if todo.due_date else ""
                category = f" category={todo.category}" if todo.category else ""
                lines.append(f"- [{todo.priority}] {todo.title}{category}{due}")
            sections.append("\n".join(lines))

        file_tasks = self._read_heartbeat_tasks()
        if file_tasks:
            sections.append("## HEARTBEAT.md\n" + file_tasks)

        if not sections:
            return None
        return (
            "【heartbeat 自动触发】你是 solo 的定时心跳 agent，当前为周期性自动检查。\n"
            "以下各事项请按规则处理：\n\n"
            "**处理规则**\n"
            "- **HEARTBEAT.md 任务**：解析任务意图并**调用工具**完成（如 solo_record、solo_process），不可只用文字回答。\n"
            "- **待确认记录**：无法代替用户决策；为每条输出一行提醒，格式：「待确认：<核心问题>」。\n"
            "- **Open Todos**：仅输出逾期或今日到期的条目，格式：「Todo 提醒：<标题>（due: <日期>）」；无逾期则不输出。\n"
            "- 若所有事项处理完毕且无需通知用户，直接返回空字符串，不要输出多余内容。\n\n"
            "---\n\n"
            + "\n\n".join(sections)
        )

    async def trigger_once(self) -> HeartbeatResult:
        agenda = self.build_agenda()
        if agenda is None:
            logger.debug("solo heartbeat skipped: empty agenda")
            return HeartbeatResult(executed=False, reason="empty")

        runner = self._runner_factory(SoloStore(self._workspace), profile=self._provider_profile)
        response = await runner.run(agenda, session_key="heartbeat")
        self._trim_heartbeat_session()
        if not response.strip():
            return HeartbeatResult(executed=True, reason="empty_response")

        target = self._pick_notify_target()
        if target is None:
            logger.info("solo heartbeat completed without notify target")
            return HeartbeatResult(executed=True, response=response, reason="no_target")

        channel, chat_id = target
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=response,
                metadata={"_session_key": "heartbeat"},
            )
        )
        return HeartbeatResult(executed=True, notified=True, response=response)

    def _read_heartbeat_tasks(self) -> str:
        if not self.heartbeat_file.exists():
            return ""
        content = self.heartbeat_file.read_text(encoding="utf-8", errors="replace")
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        ]
        return "\n".join(lines).strip()

    def _pick_notify_target(self) -> tuple[str, str] | None:
        if not self._enabled_channels:
            return None
        for item in list_conversations(self._workspace, limit=20):
            key = str(item.get("session_key") or "")
            if ":" not in key:
                continue
            channel, chat_id = key.split(":", 1)
            if channel in {"cli", "system", "heartbeat"}:
                continue
            if channel in self._enabled_channels and chat_id:
                return channel, chat_id
        return None

    def _trim_heartbeat_session(self) -> None:
        if self._keep_recent_messages <= 0:
            return
        messages, session_id = load_conversation(self._workspace, "heartbeat")
        if len(messages) > self._keep_recent_messages:
            save_conversation(
                self._workspace,
                "heartbeat",
                messages[-self._keep_recent_messages:],
                session_id=session_id,
            )
