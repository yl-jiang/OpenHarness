"""App-local heartbeat for the standalone solo gateway.

Design: signal-based watchdog that gathers deterministic signals from DB/system
state, then passes them to the LLM. The model decides what's worth notifying
and how to phrase it. Safeguards:

- **Quiet hours** (configurable, default 22:30-08:00 local): no LLM, no push.
- **Daily push cap** (configurable, default 3): cross-fingerprint hard limit.
- **Per-signal ack** (24h TTL): the same overdue todo is not re-pushed within
  a day unless its state materially changes.
- **Decision history** (7d) is injected into the LLM prompt so it knows
  "this was already pushed 2h ago" and avoids repeating itself.
- **System-health signals** (failed cron jobs, scheduler liveness) are
  collected for CLI / dashboard display but never enter the user-facing
  notification path.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

from openharness.channels.bus.events import OutboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.utils.fs import atomic_write_text
from openharness.utils.log import get_logger

from solo.runner import SoloQueryRunner
from solo.prompts import HEARTBEAT_EVAL_SYSTEM_PROMPT
from solo.core.session import list_conversations
from solo.core.store import SoloStore
from solo.core.workspace import get_workspace_root

logger = get_logger(__name__)

_HEARTBEAT_STATE_FILENAME = "heartbeat_state.json"
_PENDING_CONFIRMATION_MAX_AGE_DAYS = 7
_ACK_TTL = timedelta(hours=24)
_PUSH_HISTORY_RETENTION = timedelta(days=7)


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
    system_health: list[str] = field(default_factory=list)
    heartbeat_tasks: str = ""

    @property
    def has_user_signals(self) -> bool:
        """User-facing signals that can be formatted into notifications."""
        return bool(
            self.stale_confirmations
            or self.overdue_todos
        )

    @property
    def has_agent_tasks(self) -> bool:
        """Tasks requiring LLM execution (HEARTBEAT.md)."""
        return bool(self.heartbeat_tasks)

    @property
    def is_empty(self) -> bool:
        return not self.has_user_signals and not self.has_agent_tasks

    def iter_user_items(self) -> list[tuple[str, str]]:
        """Return (kind, message) pairs for user-facing signals."""
        items: list[tuple[str, str]] = []
        for msg in self.stale_confirmations:
            items.append(("pending_confirmation", msg))
        for msg in self.overdue_todos:
            items.append(("overdue_todo", msg))
        return items


class SoloHeartbeatService:
    """Signal-based periodic watchdog for the solo app.

    Gathers deterministic signals from DB/system state, applies quiet-hours /
    daily-cap / per-signal-ack safeguards, then passes the remainder to the
    LLM which decides what's worth notifying the user about. Decision history
    is fed back into the prompt so the LLM does not re-push the same item.
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
        quiet_hours_start: str = "22:30",
        quiet_hours_end: str = "08:00",
        timezone_name: str = "Asia/Shanghai",
        max_daily_pushes: int = 3,
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
        self._quiet_hours_start = quiet_hours_start
        self._quiet_hours_end = quiet_hours_end
        self._tz = ZoneInfo(timezone_name)
        self._max_daily_pushes = max(0, int(max_daily_pushes))
        self._state_path = self._workspace / "data" / _HEARTBEAT_STATE_FILENAME
        self._acks: dict[str, str] = {}
        self._push_history: list[dict[str, str]] = []
        self._pushes_today: int = 0
        self._push_day: str = ""
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
        self._task = asyncio.create_task(self._run_loop(), name="solo-heartbeat")
        logger.info(
            "solo heartbeat started interval_s=%d quiet=%s-%s tz=%s daily_cap=%d",
            self._interval_s,
            self._quiet_hours_start,
            self._quiet_hours_end,
            str(self._tz),
            self._max_daily_pushes,
        )

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
        self._refresh_daily_counter()
        return {
            "enabled": self._enabled,
            "interval_s": self._interval_s,
            "quiet_hours": f"{self._quiet_hours_start}-{self._quiet_hours_end}",
            "timezone": str(self._tz),
            "max_daily_pushes": self._max_daily_pushes,
            "pushes_today": self._pushes_today,
            "acked_signals": len(self._acks),
            "has_signals": not signals.is_empty,
            "notify_target": f"{target[0]}:{target[1]}" if target else "",
        }

    # ------------------------------------------------------------------
    # Quiet hours / daily cap
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_hhmm(value: str) -> time:
        text = str(value or "").strip()
        parts = text.split(":")
        if len(parts) != 2:
            return time(0, 0)
        try:
            return time(int(parts[0]) % 24, int(parts[1]) % 60)
        except ValueError:
            return time(0, 0)

    def _now_local(self) -> datetime:
        return datetime.now(self._tz)

    def _in_quiet_hours(self) -> bool:
        now_t = self._now_local().time().replace(second=0, microsecond=0)
        start = self._parse_hhmm(self._quiet_hours_start)
        end = self._parse_hhmm(self._quiet_hours_end)
        if start == end:
            return False
        if start < end:
            return start <= now_t < end
        return now_t >= start or now_t < end

    def _refresh_daily_counter(self) -> None:
        today = self._now_local().date().isoformat()
        if self._push_day != today:
            self._push_day = today
            self._pushes_today = 0

    def _daily_cap_reached(self) -> bool:
        if self._max_daily_pushes <= 0:
            return False
        self._refresh_daily_counter()
        return self._pushes_today >= self._max_daily_pushes

    # ------------------------------------------------------------------
    # Ack map / push history (persistent)
    # ------------------------------------------------------------------

    def _prune_acks(self, now: datetime) -> None:
        cutoff = (now - _ACK_TTL).isoformat()
        self._acks = {k: v for k, v in self._acks.items() if v > cutoff}

    def _prune_push_history(self, now: datetime) -> None:
        cutoff = (now - _PUSH_HISTORY_RETENTION).isoformat()
        self._push_history = [e for e in self._push_history if e.get("at", "") >= cutoff]

    def _ack_key(self, kind: str, message: str) -> str:
        normalized = " ".join(f"{kind}:{message}".split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]

    def _is_acked(self, kind: str, message: str) -> bool:
        return self._ack_key(kind, message) in self._acks

    def _ack(self, kind: str, message: str, now: datetime) -> None:
        self._acks[self._ack_key(kind, message)] = now.isoformat()

    def _record_push(self, notifications: list[str], now: datetime) -> None:
        summary = " | ".join(n.strip() for n in notifications if n.strip())[:300]
        self._push_history.append(
            {
                "at": now.isoformat(),
                "summary": summary,
                "count": len(notifications),
            }
        )
        self._refresh_daily_counter()
        self._pushes_today += len(notifications)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        acks = data.get("acks")
        if isinstance(acks, dict):
            self._acks = {str(k): str(v) for k, v in acks.items()}
        history = data.get("push_history")
        if isinstance(history, list):
            self._push_history = [e for e in history if isinstance(e, dict)]
        pushes_today = data.get("pushes_today")
        if isinstance(pushes_today, int):
            self._pushes_today = pushes_today
        push_day = data.get("push_day")
        if isinstance(push_day, str):
            self._push_day = push_day
        self._prune_acks(datetime.now(timezone.utc))
        self._prune_push_history(datetime.now(timezone.utc))

    def _save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "acks": self._acks,
            "push_history": self._push_history[-40:],
            "pushes_today": self._pushes_today,
            "push_day": self._push_day,
        }
        atomic_write_text(
            self._state_path,
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        )

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
        todos = store.list_todos(status="pending", limit=20)
        for todo in todos:
            if todo.due_date and todo.due_date <= today_str:
                signals.overdue_todos.append(
                    f"[{todo.priority}] {todo.category} {todo.title}（due: {todo.due_date}）"
                )

        # 3. System-health signals: kept out of the user notification path.
        #    They surface via CLI / dashboard (`solo heartbeat status`) only.
        for name in self._check_failed_cron_jobs():
            signals.system_health.append(f"failed_cron:{name}")
        if self._check_scheduler_down():
            signals.system_health.append("scheduler_down")

        # 5. HEARTBEAT.md tasks (optional power-user file)
        signals.heartbeat_tasks = self._read_heartbeat_tasks()

        return signals

    def _check_failed_cron_jobs(self) -> list[str]:
        """Check for recently failed cron jobs (internal-only signal)."""
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
        try:
            jobs_file = self._workspace / "data" / "cron_jobs.json"
            if not jobs_file.exists():
                return False
            from solo.gateway.cron_scheduler import is_scheduler_running
            return not is_scheduler_running()
        except Exception:
            return False

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

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def trigger_once(self) -> HeartbeatResult:
        # Safeguard 1: quiet hours — no LLM, no push.
        if self._in_quiet_hours():
            logger.debug("solo heartbeat skipped: quiet hours")
            return HeartbeatResult(executed=False, reason="quiet_hours")

        # Safeguard 2: daily push cap (cross-fingerprint).
        if self._daily_cap_reached():
            logger.info(
                "solo heartbeat skipped: daily cap reached (%d/%d)",
                self._pushes_today,
                self._max_daily_pushes,
            )
            return HeartbeatResult(executed=False, reason="daily_cap")

        signals = self.gather_signals()
        if signals.is_empty:
            logger.debug("solo heartbeat skipped: no signals")
            return HeartbeatResult(executed=False, reason="empty")

        # Safeguard 3: per-signal ack — drop items already pushed within 24h.
        unacked = [
            (kind, msg)
            for kind, msg in signals.iter_user_items()
            if not self._is_acked(kind, msg)
        ]
        has_unacked_agent_task = bool(signals.heartbeat_tasks)
        if not unacked and not has_unacked_agent_task:
            logger.debug("solo heartbeat skipped: all signals acked")
            return HeartbeatResult(executed=False, reason="all_acked")

        notifications = await self._evaluate_signals(unacked, signals.heartbeat_tasks)
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
        now = datetime.now(timezone.utc)
        for kind, msg in unacked:
            self._ack(kind, msg, now)
        self._record_push(notifications, now)
        self._save_state()
        return HeartbeatResult(executed=True, notified=True, response=user_message)

    async def _evaluate_signals(
        self,
        unacked_items: list[tuple[str, str]],
        heartbeat_tasks: str,
    ) -> list[str]:
        """Let LLM decide whether and how to notify the user based on signals."""
        parts: list[str] = []
        for kind, msg in unacked_items:
            if kind == "overdue_todo":
                label = "逾期/今日到期 Todo"
            elif kind == "pending_confirmation":
                label = "待确认记录（>24h）"
            else:
                label = kind
            parts.append(f"【{label}】\n- {msg}")
        if heartbeat_tasks:
            parts.append(f"【HEARTBEAT.md 周期性任务】\n{heartbeat_tasks}")

        signal_text = "\n\n".join(parts)
        history_text = self._format_push_history()

        prompt = (
            "【heartbeat 自动触发】你是 solo 的定时心跳 agent。\n"
            "以下是系统检测到的、尚未向用户推送过的状态信号：\n\n"
            f"{signal_text}\n\n"
            f"---\n"
            f"【过去 24h 已推送过的内容（不要重复推送，除非状态发生实质变化）】\n"
            f"{history_text}\n\n"
            "---\n"
            "**你的职责：**\n"
            "1. 判断哪些未推送的信号值得打扰用户（考虑紧急程度、可操作性、当前时段）\n"
            "2. 对值得推送的信号，用简洁友好的语言组织成通知消息\n"
            "3. 若 HEARTBEAT.md 有任务，只做提醒建议，不要执行任何写入操作\n"
            "4. **不要在深夜/清晨推送非紧急事项**（宁可空手也不要打扰）\n"
            "5. 不要推送系统内部失败（cron 失败、调度器停机等运维问题）\n\n"
            "**输出格式（严格遵守）**\n"
            '{"notifications": ["通知消息1", "通知消息2"]}\n'
            "- 每条是发给用户的一句话，简洁、有信息量、可操作\n"
            "- 语气克制、专业，不要说教或命令式措辞\n"
            "- 若所有信号都不值得打扰用户，返回 {\"notifications\": []}\n"
        )

        runner = self._runner_factory(SoloStore(self._workspace), profile=self._provider_profile)
        response = await runner.run(
            prompt,
            session_key="heartbeat",
            allow_tools=False,
            include_similar_context=False,
            use_session_history=False,
            persist_session=False,
            system_prompt_override=HEARTBEAT_EVAL_SYSTEM_PROMPT,
        )
        return self._extract_notifications(response)

    def _format_push_history(self) -> str:
        if not self._push_history:
            return "（无）"
        recent = [
            e for e in self._push_history
            if e.get("at", "") >= (datetime.now(timezone.utc) - _ACK_TTL).isoformat()
        ]
        if not recent:
            return "（无）"
        lines: list[str] = []
        for e in recent[-6:]:
            at = str(e.get("at", ""))[:19].replace("T", " ")
            summary = str(e.get("summary") or "").strip()
            if summary:
                lines.append(f"- {at}  {summary}")
        return "\n".join(lines) if lines else "（无）"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def build_agenda(self) -> str | None:
        signals = self.gather_signals()
        if signals.is_empty and not signals.system_health:
            return None
        parts: list[str] = []
        if signals.has_user_signals:
            items = [f"{k}:{m}" for k, m in signals.iter_user_items()]
            parts.append("User signals: " + "; ".join(items))
        if signals.has_agent_tasks:
            parts.append("Agent tasks: " + signals.heartbeat_tasks[:200])
        if signals.system_health:
            parts.append("System health (internal): " + "; ".join(signals.system_health))
        return "\n".join(parts)

    @staticmethod
    def _extract_notifications(response: str) -> list[str]:
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
