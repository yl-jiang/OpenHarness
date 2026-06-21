"""Tests for the GoalMode state machine in openharness.goal.state."""

from __future__ import annotations

import pytest

from openharness.goal.state import (
    GOAL_STATE_KEY,
    GoalBudgetLimits,
    GoalMode,
    GoalSnapshot,
)


@pytest.fixture
def metadata() -> dict:
    return {}


@pytest.fixture
def goal_mode(metadata: dict) -> GoalMode:
    return GoalMode(metadata)


def test_create_goal(goal_mode: GoalMode, metadata: dict) -> None:
    snap = goal_mode.create_goal("Ship feature X")
    assert isinstance(snap, GoalSnapshot)
    assert snap.status == "active"
    assert snap.objective == "Ship feature X"
    assert snap.turns_used == 0
    assert snap.tokens_used == 0
    assert snap.wall_clock_ms >= 0
    assert GOAL_STATE_KEY in metadata
    # GoalMode round-trips through the serialized dict in tool_metadata.
    restored = GoalMode(metadata)
    assert restored.get_goal() is not None
    assert restored.get_goal().objective == "Ship feature X"


def test_create_goal_replaces_existing(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("first")
    with pytest.raises(ValueError):
        goal_mode.create_goal("second")
    snap = goal_mode.create_goal("second", replace=True)
    assert snap.objective == "second"


def test_create_goal_empty_objective_raises(goal_mode: GoalMode) -> None:
    with pytest.raises(ValueError):
        goal_mode.create_goal("")
    with pytest.raises(ValueError):
        goal_mode.create_goal("   ")


def test_create_goal_completion_criterion(goal_mode: GoalMode) -> None:
    snap = goal_mode.create_goal(
        "Ship feature X", completion_criterion="All tests pass"
    )
    assert snap.completion_criterion == "All tests pass"


def test_pause_resume_cycle(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    paused = goal_mode.pause_goal(reason="user requested", actor="user")
    assert paused.status == "paused"
    assert paused.terminal_reason == "user requested"

    resumed = goal_mode.resume_goal(actor="user")
    assert resumed.status == "active"


def test_cancel_clears_state(goal_mode: GoalMode, metadata: dict) -> None:
    goal_mode.create_goal("do thing")
    snapshot = goal_mode.cancel_goal()
    assert snapshot is not None
    # cancel deletes the record — no "cancelled" status anywhere.
    assert goal_mode.get_goal() is None
    assert GOAL_STATE_KEY not in metadata


def test_mark_complete_keeps_record_until_cleared(goal_mode: GoalMode, metadata: dict) -> None:
    goal_mode.create_goal("do thing")
    final = goal_mode.mark_complete(reason="all done", actor="model")
    assert final is not None
    assert final.status == "complete"
    # After mark_complete the record still exists with status="complete";
    # the driver must call clear_after_complete() to finalize.
    snapshot = goal_mode.get_goal()
    assert snapshot is not None
    assert snapshot.status == "complete"
    goal_mode.clear_after_complete()
    assert goal_mode.get_goal() is None
    assert GOAL_STATE_KEY not in metadata


def test_complete_status_discarded_on_restore(metadata: dict) -> None:
    """If a snapshot was saved mid-completion (status="complete"), restore
    treats it as absent — complete is transient."""
    metadata[GOAL_STATE_KEY] = {
        "goal_id": "x",
        "objective": "was being completed",
        "status": "complete",
    }
    gm = GoalMode(metadata)
    assert gm.get_goal() is None


def test_mark_blocked_stays_resumable(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    blocked = goal_mode.mark_blocked(reason="api error", actor="runtime")
    assert blocked.status == "blocked"
    assert blocked.terminal_reason == "api error"
    # Blocked goals can be resumed later.
    resumed = goal_mode.resume_goal()
    assert resumed.status == "active"


def test_get_active_goal_only_when_active(goal_mode: GoalMode) -> None:
    assert goal_mode.get_active_goal() is None
    goal_mode.create_goal("do thing")
    assert goal_mode.get_active_goal() is not None
    goal_mode.pause_goal()
    assert goal_mode.get_active_goal() is None
    assert goal_mode.get_goal() is not None  # paused snapshot still visible


def test_budget_over_budget(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.set_budget_limits(GoalBudgetLimits(turn_budget=2))
    goal_mode.increment_turn()
    snap = goal_mode.increment_turn()
    assert snap.budget.over_budget is True
    assert snap.budget.remaining_turns == 0


def test_budget_usage_fraction(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.set_budget_limits(GoalBudgetLimits(turn_budget=4))
    goal_mode.increment_turn()
    snap = goal_mode.increment_turn()
    # 2/4 = 0.5 — well within budget.
    assert 0.49 <= snap.budget.usage_fraction <= 0.51


def test_normalize_after_replay_active_to_paused(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    # Simulate a process restart: state restored from persisted dict.
    goal_mode.normalize_after_replay()
    snap = goal_mode.get_goal()
    assert snap.status == "paused"
    assert snap.last_actor == "runtime"
    assert snap.terminal_reason == "Paused after agent resume"


def test_normalize_after_replay_no_goal(goal_mode: GoalMode) -> None:
    # No-op when nothing is stored.
    goal_mode.normalize_after_replay()
    assert goal_mode.get_goal() is None


def test_persist_and_restore(goal_mode: GoalMode, metadata: dict) -> None:
    goal_mode.create_goal(
        "Ship feature X",
        completion_criterion="All tests pass",
    )
    goal_mode.set_budget_limits(GoalBudgetLimits(turn_budget=10, token_budget=1000))
    goal_mode.increment_turn()
    goal_mode.record_token_usage(123)

    # Build a fresh GoalMode over the same metadata — should restore.
    restored = GoalMode(metadata)
    snap = restored.get_goal()
    assert snap is not None
    assert snap.objective == "Ship feature X"
    assert snap.completion_criterion == "All tests pass"
    assert snap.turns_used == 1
    assert snap.tokens_used == 123
    assert snap.budget.turn_budget == 10
    assert snap.budget.token_budget == 1000


def test_restore_corrupt_state_discarded(metadata: dict) -> None:
    metadata[GOAL_STATE_KEY] = {"garbage": True}
    gm = GoalMode(metadata)
    assert gm.get_goal() is None
    assert GOAL_STATE_KEY not in metadata


def test_restore_invalid_status_discarded(metadata: dict) -> None:
    metadata[GOAL_STATE_KEY] = {
        "goal_id": "x",
        "objective": "a real objective",
        "status": "completed",  # not a durable status
    }
    gm = GoalMode(metadata)
    assert gm.get_goal() is None


def test_resume_idempotent_when_active(goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    snap = goal_mode.resume_goal()
    assert snap.status == "active"


def test_no_goal_operations_are_safe(goal_mode: GoalMode) -> None:
    assert goal_mode.get_goal() is None
    assert goal_mode.cancel_goal() is None
    assert goal_mode.mark_complete() is None
    assert goal_mode.mark_blocked() is None
    assert goal_mode.record_token_usage(10) is None
    assert goal_mode.increment_turn() is None
    with pytest.raises(ValueError):
        goal_mode.pause_goal()
    with pytest.raises(ValueError):
        goal_mode.resume_goal()
    with pytest.raises(ValueError):
        goal_mode.set_budget_limits(GoalBudgetLimits(turn_budget=5))
