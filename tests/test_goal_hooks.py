"""Tests for goal-lifecycle hook event wiring (Phase 5)."""

from __future__ import annotations

from typing import Any

import pytest

from openharness.goal.state import GoalMode
from openharness.hooks import HookEvent


class _FakeHookExecutor:
    """Captures hook invocations for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[HookEvent, dict[str, Any]]] = []
        self.raise_on: HookEvent | None = None

    async def execute(self, event: HookEvent, payload: dict[str, Any]):
        if self.raise_on is not None and event == self.raise_on:
            raise RuntimeError(f"boom on {event.value}")
        self.calls.append((event, payload))


@pytest.fixture
def executor() -> _FakeHookExecutor:
    return _FakeHookExecutor()


@pytest.fixture
def goal_mode(executor: _FakeHookExecutor) -> GoalMode:
    metadata: dict = {}
    return GoalMode(metadata, hook_executor=executor)


@pytest.mark.asyncio
async def test_create_goal_enqueues_created_hook(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("Ship feature X", actor="user")
    await goal_mode.flush_hooks()
    assert len(executor.calls) == 1
    event, payload = executor.calls[0]
    assert event == HookEvent.GOAL_CREATED
    assert payload["event"] == "goal_created"
    assert payload["goal"]["objective"] == "Ship feature X"
    assert payload["actor"] == "user"


@pytest.mark.asyncio
async def test_mark_complete_enqueues_completed_hook(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    # mark_complete leaves status="complete" on the record; driver calls
    # clear_after_complete() afterwards.
    goal_mode.mark_complete(reason="all green", actor="model")
    await goal_mode.flush_hooks()
    events = [e for e, _ in executor.calls]
    assert HookEvent.GOAL_CREATED in events
    assert HookEvent.GOAL_COMPLETED in events
    completed_payload = next(p for e, p in executor.calls if e == HookEvent.GOAL_COMPLETED)
    assert completed_payload["reason"] == "all green"
    assert completed_payload["actor"] == "model"


@pytest.mark.asyncio
async def test_mark_blocked_carries_reason_in_payload(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.mark_blocked(reason="API key missing", actor="runtime")
    await goal_mode.flush_hooks()
    payload = next(p for e, p in executor.calls if e == HookEvent.GOAL_BLOCKED)
    assert payload["reason"] == "API key missing"
    assert payload["actor"] == "runtime"


@pytest.mark.asyncio
async def test_cancel_goal_enqueues_cancelled_hook(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.cancel_goal(actor="user")
    await goal_mode.flush_hooks()
    events = [e for e, _ in executor.calls]
    assert HookEvent.GOAL_CANCELLED in events


@pytest.mark.asyncio
async def test_pause_resume_enqueues_events(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.pause_goal(reason="need input", actor="user")
    goal_mode.resume_goal(reason="ready", actor="user")
    await goal_mode.flush_hooks()
    events = [e for e, _ in executor.calls]
    assert events == [
        HookEvent.GOAL_CREATED,
        HookEvent.GOAL_PAUSED,
        HookEvent.GOAL_RESUMED,
    ]


@pytest.mark.asyncio
async def test_flush_hooks_clears_queue(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    await goal_mode.flush_hooks()
    assert len(executor.calls) == 1
    # Second flush is a no-op.
    await goal_mode.flush_hooks()
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_flush_noop_when_no_executor() -> None:
    metadata: dict = {}
    gm = GoalMode(metadata)  # no hook_executor
    gm.create_goal("do thing")
    # Must not raise even though there is no executor.
    await gm.flush_hooks()


@pytest.mark.asyncio
async def test_flush_swallows_hook_exceptions(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    # First event raises; subsequent events must still fire.
    executor.raise_on = HookEvent.GOAL_CREATED
    goal_mode.create_goal("do thing")
    goal_mode.pause_goal()
    await goal_mode.flush_hooks()
    # GOAL_CREATED was dropped (executor blew up), GOAL_PAUSED succeeded.
    events = [e for e, _ in executor.calls]
    assert events == [HookEvent.GOAL_PAUSED]


@pytest.mark.asyncio
async def test_multiple_state_changes_flush_in_order(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.pause_goal()
    goal_mode.resume_goal()
    goal_mode.mark_blocked(reason="stuck")
    await goal_mode.flush_hooks()
    events = [e for e, _ in executor.calls]
    assert events == [
        HookEvent.GOAL_CREATED,
        HookEvent.GOAL_PAUSED,
        HookEvent.GOAL_RESUMED,
        HookEvent.GOAL_BLOCKED,
    ]


@pytest.mark.asyncio
async def test_resume_idempotent_does_not_enqueue(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal("do thing")
    # Goal is already active — resume_goal is a no-op (returns existing snapshot).
    goal_mode.resume_goal()
    await goal_mode.flush_hooks()
    events = [e for e, _ in executor.calls]
    # Only the create event, no resumed event.
    assert events == [HookEvent.GOAL_CREATED]


@pytest.mark.asyncio
async def test_hook_payload_includes_goal_snapshot(
    goal_mode: GoalMode, executor: _FakeHookExecutor
) -> None:
    goal_mode.create_goal(
        "Ship feature X", completion_criterion="All tests pass", actor="user"
    )
    await goal_mode.flush_hooks()
    payload = executor.calls[0][1]
    # Snapshot fields must be present in the payload.
    goal = payload["goal"]
    assert goal["objective"] == "Ship feature X"
    assert goal["completion_criterion"] == "All tests pass"
    assert goal["status"] == "active"
    assert "goal_id" in goal
