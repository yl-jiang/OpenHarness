"""Shared helpers for app-local one-shot reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo, timezone
from math import ceil


@dataclass(frozen=True)
class ReminderSchedule:
    """Normalized reminder timing details."""

    due_at_utc: datetime
    due_at_local: datetime
    delay_text: str


def build_one_shot_reminder_schedule(
    *,
    remind_at: str | None = None,
    delay_seconds: int | str | None = None,
    delay_minutes: int | str | None = None,
    delay_hours: int | str | None = None,
    delay_days: int | str | None = None,
    now: datetime | None = None,
) -> ReminderSchedule:
    """Return a normalized one-shot reminder schedule.

    Exactly one of these schedule styles must be provided:
    - ``remind_at`` as an ISO-8601 datetime
    - one or more relative delays (seconds / minutes / hours / days)
    """

    if now is None:
        current_local = datetime.now().astimezone()
    elif now.tzinfo is None:
        current_local = now.replace(tzinfo=timezone.utc)
    else:
        current_local = now
    has_absolute = bool(str(remind_at or "").strip())
    relative_values = {
        "delay_seconds": delay_seconds,
        "delay_minutes": delay_minutes,
        "delay_hours": delay_hours,
        "delay_days": delay_days,
    }
    has_relative = any(value is not None for value in relative_values.values())

    if has_absolute and has_relative:
        raise ValueError("Use either remind_at or delay_* fields, not both.")
    if not has_absolute and not has_relative:
        raise ValueError("Reminder requires remind_at or at least one delay_* field.")

    if has_absolute:
        due_local = _parse_remind_at(str(remind_at), current_local.tzinfo)
    else:
        total_seconds = (
            _coerce_non_negative_int(delay_seconds, "delay_seconds")
            + _coerce_non_negative_int(delay_minutes, "delay_minutes") * 60
            + _coerce_non_negative_int(delay_hours, "delay_hours") * 3600
            + _coerce_non_negative_int(delay_days, "delay_days") * 86400
        )
        if total_seconds <= 0:
            raise ValueError("Reminder delay must be greater than zero.")
        due_local = current_local + timedelta(seconds=total_seconds)

    if due_local <= current_local:
        raise ValueError("Reminder time must be in the future.")

    return ReminderSchedule(
        due_at_utc=due_local.astimezone(timezone.utc),
        due_at_local=due_local,
        delay_text=humanize_delay(due_local - current_local),
    )


def format_local_reminder_time(value: datetime) -> str:
    """Format a reminder timestamp for user-facing confirmation text."""

    local_value = value if value.tzinfo is not None else value.astimezone()
    fmt = "%Y-%m-%d %H:%M:%S" if local_value.second else "%Y-%m-%d %H:%M"
    return local_value.strftime(fmt)


def humanize_delay(delta: timedelta) -> str:
    """Return a short Chinese phrase like ``2 分钟后``."""

    total_seconds = max(1, ceil(delta.total_seconds()))
    if total_seconds % 86400 == 0:
        days = total_seconds // 86400
        return f"{days} 天后"
    if total_seconds % 3600 == 0:
        hours = total_seconds // 3600
        return f"{hours} 小时后"
    if total_seconds % 60 == 0:
        minutes = total_seconds // 60
        return f"{minutes} 分钟后"
    if total_seconds >= 60:
        minutes = ceil(total_seconds / 60)
        return f"约 {minutes} 分钟后"
    return f"{total_seconds} 秒后"


def _parse_remind_at(value: str, default_tz: tzinfo | None) -> datetime:
    raw = value.strip()
    if not raw:
        raise ValueError("remind_at is required when no delay_* field is provided.")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:  # pragma: no cover - exercised via caller tests
        raise ValueError("remind_at must be an ISO-8601 datetime string.") from exc
    if parsed.tzinfo is None:
        if default_tz is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.replace(tzinfo=default_tz)
    return parsed.astimezone(default_tz or timezone.utc)


def _coerce_non_negative_int(value: int | str | None, field_name: str) -> int:
    if value is None:
        return 0
    coerced = int(value)
    if coerced < 0:
        raise ValueError(f"{field_name} must be >= 0")
    return coerced
