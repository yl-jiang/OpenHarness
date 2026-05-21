from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from openharness.services.app_reminders import build_one_shot_reminder_schedule


def test_build_one_shot_reminder_schedule_from_relative_delay() -> None:
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 5, 21, 14, 0, 0, tzinfo=local_tz)

    schedule = build_one_shot_reminder_schedule(delay_minutes=2, now=now)

    assert schedule.delay_text == "2 分钟后"
    assert schedule.due_at_local == datetime(2026, 5, 21, 14, 2, 0, tzinfo=local_tz)
    assert schedule.due_at_utc == datetime(2026, 5, 21, 6, 2, 0, tzinfo=timezone.utc)


def test_build_one_shot_reminder_schedule_uses_local_timezone_for_naive_remind_at() -> None:
    local_tz = timezone(timedelta(hours=8))
    now = datetime(2026, 5, 21, 14, 0, 0, tzinfo=local_tz)

    schedule = build_one_shot_reminder_schedule(
        remind_at="2026-05-21T14:30:00",
        now=now,
    )

    assert schedule.due_at_local == datetime(2026, 5, 21, 14, 30, 0, tzinfo=local_tz)
    assert schedule.due_at_utc == datetime(2026, 5, 21, 6, 30, 0, tzinfo=timezone.utc)


def test_build_one_shot_reminder_schedule_rejects_mixed_absolute_and_relative() -> None:
    with pytest.raises(ValueError, match="either remind_at or delay_\\*"):
        build_one_shot_reminder_schedule(remind_at="2026-05-21T14:30:00+08:00", delay_minutes=2)
