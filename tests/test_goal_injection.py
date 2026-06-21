"""Tests for goal/injection.py — reminder and prompt builders."""

from __future__ import annotations

import pytest

from openharness.goal.injection import (
    GOAL_CANCELLED_REMINDER,
    GOAL_CONTINUATION_PROMPT,
    build_blocked_reason_prompt,
    build_completion_summary_prompt,
    build_goal_reminder,
    escape_untrusted_text,
)
from openharness.goal.state import GoalBudgetLimits, GoalMode, GoalSnapshot


def _make_snapshot(status: str = "active", **overrides) -> GoalSnapshot:
    """Build a snapshot by going through GoalMode for realism."""
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal(
        overrides.pop("objective", "Ship feature X"),
        completion_criterion=overrides.pop("completion_criterion", "All tests pass"),
    )
    for _ in range(overrides.pop("extra_turns", 0)):
        gm.increment_turn()
    for _ in range(overrides.pop("extra_tokens", 0)):
        gm.record_token_usage(1)
    if overrides.get("budget_limits"):
        gm.set_budget_limits(overrides["budget_limits"])
    reason = overrides.get("reason")
    if status == "paused":
        gm.pause_goal(reason=reason)
    elif status == "blocked":
        gm.mark_blocked(reason=reason)
    elif status == "complete":
        return gm.mark_complete(reason=reason)
    return gm.get_goal()


@pytest.fixture
def snap_active_done() -> GoalSnapshot:
    return _make_snapshot(status="complete", reason="all green")


def test_escape_untrusted_text() -> None:
    assert escape_untrusted_text("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    assert escape_untrusted_text("<script>x&y</script>") == (
        "&lt;script&gt;x&amp;y&lt;/script&gt;"
    )


def test_build_goal_reminder_none_when_no_goal() -> None:
    assert build_goal_reminder(None) is None


def test_active_reminder_contains_objective_and_status() -> None:
    snap = _make_snapshot(status="active")
    reminder = build_goal_reminder(snap)
    assert reminder is not None
    assert "Ship feature X" in reminder
    assert "All tests pass" in reminder
    assert "active" in reminder
    assert "untrusted_objective" in reminder
    assert "untrusted_completion_criterion" in reminder
    assert "Budget guidance" in reminder


def test_active_reminder_no_criterion_omits_tag() -> None:
    snap = _make_snapshot(status="active", completion_criterion=None)
    reminder = build_goal_reminder(snap)
    assert "untrusted_completion_criterion" not in reminder


def test_paused_note_contains_criterion() -> None:
    snap = _make_snapshot(status="paused", reason="user requested")
    reminder = build_goal_reminder(snap)
    assert "paused" in reminder
    assert "untrusted_objective" in reminder
    # Design §5.4: paused note still carries the completion criterion.
    assert "untrusted_completion_criterion" in reminder
    assert "All tests pass" in reminder


def test_blocked_note_contains_criterion_and_reason() -> None:
    snap = _make_snapshot(status="blocked", reason="API key missing")
    reminder = build_goal_reminder(snap)
    assert "blocked" in reminder
    assert "API key missing" in reminder
    # Blocked note also carries the criterion (matches kimi-code buildBlockedNote).
    assert "untrusted_completion_criterion" in reminder
    assert "All tests pass" in reminder


def test_untrusted_objective_escaping_in_reminder() -> None:
    snap = _make_snapshot(
        status="active",
        objective="A & B < C > D",
        completion_criterion="x < y",
    )
    reminder = build_goal_reminder(snap)
    assert "&amp;" in reminder
    assert "&lt;" in reminder
    assert "&gt;" in reminder
    assert "< C >" not in reminder


def test_usage_fraction_uses_used_over_total() -> None:
    # 3 turns used out of 4 budget => usage_fraction = 0.75
    snap = _make_snapshot(
        status="active",
        extra_turns=3,
        budget_limits=GoalBudgetLimits(turn_budget=4),
    )
    assert 0.74 <= snap.budget.usage_fraction <= 0.76
    reminder = build_goal_reminder(snap)
    # >= 75% → "nearing a budget"
    assert "nearing a budget" in reminder


def test_within_budget_guidance() -> None:
    snap = _make_snapshot(
        status="active",
        extra_turns=1,
        budget_limits=GoalBudgetLimits(turn_budget=10),
    )
    reminder = build_goal_reminder(snap)
    assert "within budget" in reminder


def test_continuation_prompt_and_cancelled_reminder_are_strings() -> None:
    assert isinstance(GOAL_CONTINUATION_PROMPT, str)
    assert "UpdateGoal" in GOAL_CONTINUATION_PROMPT
    assert isinstance(GOAL_CANCELLED_REMINDER, str)
    assert "cancelled" in GOAL_CANCELLED_REMINDER


def test_completion_summary_prompt(snap_active_done: GoalSnapshot) -> None:
    text = build_completion_summary_prompt(snap_active_done)
    assert "complete" in text.lower()
    assert snap_active_done.objective in text
    assert "turns" in text


def test_blocked_reason_prompt_includes_reason() -> None:
    snap = _make_snapshot(status="blocked", reason="missing API key")
    text = build_blocked_reason_prompt(snap)
    assert "missing API key" in text
    assert "blocked" in text.lower()
