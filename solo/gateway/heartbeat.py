"""App-local heartbeat for the standalone solo gateway.

Design: signal-based watchdog that gathers deterministic signals from DB/system
state, then passes them ALL to the LLM.  The model decides what's worth
notifying and how to phrase it — user experience over cost.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import get_logger

from solo.runner import SoloQueryRunner
from solo.core.session import list_conversations
from solo.core.store import SoloStore
from solo.core.workspace import get_workspace_root

logger = get_logger(__name__)

_HEARTBEAT_STATE_FILENAME = "heartbeat_state.json"
_PENDING_CONFIRMATION_MAX_AGE_DAYS = 7
_COOLDOWN_MIN_SECONDS = 30 * 60
_COOLDOWN_INTERVAL_MULTIPLIER = 4
_HEARTBEAT_EVAL_SYSTEM_PROMPT = (
    "你是 solo heartbeat 的只读通知评估助手。"
    "你不能调用任何工具，也不能写入记录、创建待办或修改数据。"
    "你只能基于用户消息里的信号生成 JSON 结果。"
)


class _Runner(Protocol):
    async def run(self, user_text: str, session_key: str = "", **kwargs: object) -> str: ...


@dataclass(frozen=True)
class HeartbeatResult:
    executed: bool
    notified: bool = False
    reason: str = ""
    response: str = ""


@dataclass
class HeartbeatSignals:
    """Deterministic signals gathered without any LLM call."""

    stale_confirmations: list[str] = field(default_factory=list)
    overdue_todos: list[str] = field(default_factory=list)
    failed_cron_jobs: list[str] = field(default_factory=list)
    scheduler_down: bool = False
    heartbeat_tasks: str = ""  # raw HEARTBEAT.md content (requires LLM)

    @property
    def has_simple_signals(self) -> bool:
        """Signals that can be formatted without LLM."""
        return bool(
            self.stale_confirmations
            or self.overdue_todos
            or self.failed_cron_jobs
            or self.scheduler_down
        )

    @property
    def has_agent_tasks(self) -> bool:
        """Tasks requiring LLM execution (HEARTBEAT.md)."""
        return bool(self.heartbeat_tasks)

    @property
    def is_empty(self) -> bool:
        return not self.has_simple_signals and not self.has_agent_tasks

    def format_notifications(self) -> list[str]:
        """Format simple signals into user-facing notification strings."""
        items: list[str] = []
        for msg in self.stale_confirmations:
            items.append(f"待确认：{msg}")
        for msg in self.overdue_todos:
            items.append(f"Todo 提醒：{msg}")
        for msg in self.failed_cron_jobs:
            items.append(f"⚠️ 定时任务失败：{msg}")
        if self.scheduler_down:
            items.append("⚠️ 定时任务调度器已停止运行，提醒和定时任务将不会执行。")
        return items


class SoloHeartbeatService:
    """Signal-based periodic watchdog for the solo app.

    Gathers deterministic signals from DB/system state, then passes ALL
    signals to the LLM which decides what's worth notifying the user about
    and how to phrase it.  User experience over cost.
    """

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
        notification_cooldown_s: int | None = None,
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
        default_cooldown = max(_COOLDOWN_MIN_SECONDS, self._interval_s * _COOLDOWN_INTERVAL_MULTIPLIER)
        self._notification_cooldown_s = max(
            0,
            int(default_cooldown if notification_cooldown_s is None else notification_cooldown_s),
        )
        self._state_path = self._workspace / "data" / _HEARTBEAT_STATE_FILENAME
        self._last_signal_fingerprint = ""
        self._last_notified_at: datetime | None = None
        self._load_state()
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
        self._task = asyncio.create_task(self._run_loop(), name="wolo-heartbeat")
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
        signals = self.gather_signals()
        target = self._pick_notify_target()
        return {
            "enabled": self._enabled,
            "interval_s": self._interval_s,
            "notification_cooldown_s": self._notification_cooldown_s,
            "has_signals": not signals.is_empty,
            "notify_target": f"{target[0]}:{target[1]}" if target else "",
        }

    # ------------------------------------------------------------------
    # Signal gathering (deterministic, no LLM)
    # ------------------------------------------------------------------

    def gather_signals(self) -> HeartbeatSignals:
        """Collect all heartbeat signals from DB and system state."""
        store = SoloStore(self._workspace)
        signals = HeartbeatSignals()

        # 1. Stale pending confirmations (waiting > 24h)
        pending = store.list_pending_confirmations()
        for item in pending:
            if not self._is_recent(item.created_at, max_age_days=_PENDING_CONFIRMATION_MAX_AGE_DAYS):
                continue
            question = item.questions[0] if item.questions else item.clarification_reason
            signals.stale_confirmations.append(
                f"{item.raw_content[:60]}（{question}）"
            )
            if len(signals.stale_confirmations) >= 5:
                break

        # 2. Overdue / due-today todos
        today_str = date.today().isoformat()
        todos = store.list_todos(status="pending", limit=10)
        for todo in todos:
            if todo.due_date and todo.due_date <= today_str:
                signals.overdue_todos.append(
                    f"{todo.title}（due: {todo.due_date}）"
                )


        # 4. Failed cron jobs (check recent history)
        signals.failed_cron_jobs = self._check_failed_cron_jobs()

        # 5. Scheduler liveness
        signals.scheduler_down = self._check_scheduler_down()

        # 6. HEARTBEAT.md tasks (optional power-user file)
        signals.heartbeat_tasks = self._read_heartbeat_tasks()

        return signals

    def _check_failed_cron_jobs(self) -> list[str]:
        """Check for recently failed cron jobs."""
        history_path = self._workspace / "data" / "cron_history.jsonl"
        if not history_path.exists():
            return []
        failures: list[str] = []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        try:
            lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in reversed(lines[-20:]):
                if not line.strip():
                    continue
                entry = json.loads(line)
                if entry.get("status") == "failed" and entry.get("ended_at", "") >= cutoff:
                    failures.append(
                        str(
                            entry.get("name")
                            or entry.get("job_name")
                            or entry.get("job_id")
                            or "unknown"
                        )
                    )
        except Exception:
            pass
        return failures[:3]

    def _check_scheduler_down(self) -> bool:
        """Check if the cron scheduler daemon is running (only relevant if jobs exist)."""
        try:
            jobs_file = self._workspace / "data" / "cron_jobs.json"
            if not jobs_file.exists():
                return False
            from solo.gateway.cron_scheduler import is_scheduler_running
            return not is_scheduler_running()
        except Exception:
            return False

    def _read_heartbeat_tasks(self) -> str:
        """Read optional HEARTBEAT.md for power-user agent tasks."""
        if not self.heartbeat_file.exists():
            return ""
        content = self.heartbeat_file.read_text(encoding="utf-8", errors="replace")
        lines = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.strip().startswith("<!--")
        ]
        return "\n".join(lines).strip()

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def trigger_once(self) -> HeartbeatResult:
        signals = self.gather_signals()

        if signals.is_empty:
            logger.debug("solo heartbeat skipped: no signals")
            return HeartbeatResult(executed=False, reason="empty")

        signal_fingerprint = self._signal_fingerprint(signals)
        if self._should_suppress(signal_fingerprint):
            logger.info("solo heartbeat suppressed duplicate signals within cooldown")
            return HeartbeatResult(executed=False, reason="cooldown")

        # All signals go through LLM — it decides what/how to notify
        notifications = await self._evaluate_signals(signals)

        if not notifications:
            return HeartbeatResult(executed=True, reason="empty_response")

        user_message = "\n".join(notifications)
        target = self._pick_notify_target()
        if target is None:
            logger.info("solo heartbeat completed without notify target")
            return HeartbeatResult(executed=True, response=user_message, reason="no_target")

        channel, chat_id = target
        await self._bus.publish_outbound(
            OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=user_message,
                metadata={"_session_key": "heartbeat"},
            )
        )
        self._mark_notified(signal_fingerprint)
        return HeartbeatResult(executed=True, notified=True, response=user_message)

    async def _evaluate_signals(self, signals: HeartbeatSignals) -> list[str]:
        """Let LLM decide whether and how to notify the user based on signals."""
        parts: list[str] = []

        if signals.overdue_todos:
            parts.append("【逾期/今日到期 Todo】\n" + "\n".join(f"- {t}" for t in signals.overdue_todos))
        if signals.stale_confirmations:
            parts.append("【待确认记录（>24h）】\n" + "\n".join(f"- {c}" for c in signals.stale_confirmations))
        if signals.failed_cron_jobs:
            parts.append("【失败的定时任务】\n" + "\n".join(f"- {j}" for j in signals.failed_cron_jobs))
        if signals.scheduler_down:
            parts.append("【系统异常】定时任务调度器已停止运行")
        if signals.heartbeat_tasks:
            parts.append(f"【HEARTBEAT.md 周期性任务】\n{signals.heartbeat_tasks}")

        signal_text = "\n\n".join(parts)

        prompt = (
            "【heartbeat 自动触发】你是 solo 的定时心跳 agent。\n"
            "以下是系统检测到的当前状态信号：\n\n"
            f"{signal_text}\n\n"
            "---\n"
            "**你的职责：**\n"
            "1. 判断哪些信号值得通知用户（考虑紧急程度、可操作性）\n"
            "2. 对值得通知的信号，用简洁友好的语言组织成通知消息\n"
            "3. 若 HEARTBEAT.md 有任务，只做提醒建议，不要执行任何写入操作\n\n"
            "**输出格式（严格遵守）**\n"
            '{"notifications": ["通知消息1", "通知消息2"]}\n'
            "- 每条是发给用户的一句话，简洁、有信息量、可操作\n"
            "- 语气克制、专业，不要说教或命令式措辞（如“立刻”“必须”“身体垮了”）\n"
            "- 若所有信号都不值得打扰用户（如：不紧急、用户无法立即行动），"
            '返回 {"notifications": []}\n'
        )

        runner = self._runner_factory(SoloStore(self._workspace), profile=self._provider_profile)
        response = await runner.run(
            prompt,
            session_key="heartbeat",
            allow_tools=False,
            include_similar_context=False,
            use_session_history=False,
            persist_session=False,
            system_prompt_override=_HEARTBEAT_EVAL_SYSTEM_PROMPT,
        )
        return self._extract_notifications(response)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Keep build_agenda for backward compat (used by `solo heartbeat status`)
    def build_agenda(self) -> str | None:
        signals = self.gather_signals()
        if signals.is_empty:
            return None
        parts: list[str] = []
        if signals.has_simple_signals:
            parts.append("Simple signals: " + "; ".join(signals.format_notifications()))
        if signals.has_agent_tasks:
            parts.append("Agent tasks: " + signals.heartbeat_tasks[:200])
        return "\n".join(parts)

    @staticmethod
    def _extract_notifications(response: str) -> list[str]:
        """Parse JSON response from heartbeat agent."""
        text = response.strip()
        if not text:
            return []

        json_match = re.search(r'\{[^{}]*"notifications"\s*:\s*\[.*?\][^{}]*\}', text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            logger.debug("heartbeat response is not valid JSON, suppressing")
            return []

        if not isinstance(data, dict):
            return []
        items = data.get("notifications", [])
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if isinstance(item, str) and item.strip()]

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

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    def _is_recent(self, value: str, *, max_age_days: int) -> bool:
        parsed = self._parse_iso_datetime(value)
        if parsed is None:
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        return parsed >= cutoff

    def _signal_fingerprint(self, signals: HeartbeatSignals) -> str:
        payload = {
            "stale_confirmations": sorted(signals.stale_confirmations),
            "overdue_todos": sorted(signals.overdue_todos),
            "failed_cron_jobs": sorted(signals.failed_cron_jobs),
            "scheduler_down": signals.scheduler_down,
            "heartbeat_tasks": signals.heartbeat_tasks.strip(),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _should_suppress(self, fingerprint: str) -> bool:
        if self._notification_cooldown_s <= 0:
            return False
        if not self._last_signal_fingerprint or self._last_signal_fingerprint != fingerprint:
            return False
        if self._last_notified_at is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._last_notified_at).total_seconds()
        return elapsed < self._notification_cooldown_s

    def _mark_notified(self, fingerprint: str) -> None:
        self._last_signal_fingerprint = fingerprint
        self._last_notified_at = datetime.now(timezone.utc)
        self._save_state()

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        self._last_signal_fingerprint = str(data.get("last_signal_fingerprint") or "")
        notified_at = self._parse_iso_datetime(str(data.get("last_notified_at") or ""))
        self._last_notified_at = notified_at

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "last_signal_fingerprint": self._last_signal_fingerprint,
            "last_notified_at": (
                self._last_notified_at.isoformat() if self._last_notified_at is not None else ""
            ),
        }
        atomic_write_text(
            self._state_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )
