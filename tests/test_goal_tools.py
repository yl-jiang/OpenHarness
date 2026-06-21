"""Tests for the four goal tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.tools.base import ToolExecutionContext
from openharness.tools.create_goal_tool import CreateGoalTool, CreateGoalToolInput
from openharness.tools.get_goal_tool import GetGoalTool, GetGoalToolInput
from openharness.tools.set_goal_budget_tool import SetGoalBudgetTool, SetGoalBudgetToolInput
from openharness.tools.update_goal_tool import (
    GOAL_STOP_TURN_KEY,
    UpdateGoalTool,
    UpdateGoalToolInput,
)


@pytest.fixture
def metadata() -> dict:
    return {}


@pytest.fixture
def goal_mode(metadata: dict) -> GoalMode:
    gm = GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = gm
    return gm


def _context(metadata: dict) -> ToolExecutionContext:
    return ToolExecutionContext(cwd=Path.cwd(), metadata=metadata)


async def _run(tool, input_cls, metadata, **kwargs):
    return await tool.execute(input_cls(**kwargs), _context(metadata))


# --------------------------------------------------------------- CreateGoalTool


@pytest.mark.asyncio
async def test_create_goal_tool(metadata: dict, goal_mode: GoalMode) -> None:
    tool = CreateGoalTool()
    result = await _run(
        tool,
        CreateGoalToolInput,
        metadata,
        objective="Ship feature X",
        completion_criterion="All tests pass",
    )
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["goal"]["objective"] == "Ship feature X"
    assert payload["goal"]["completion_criterion"] == "All tests pass"
    assert payload["goal"]["status"] == "active"


@pytest.mark.asyncio
async def test_create_goal_tool_replace_required(metadata: dict, goal_mode: GoalMode) -> None:
    tool = CreateGoalTool()
    await _run(tool, CreateGoalToolInput, metadata, objective="first")
    result = await _run(tool, CreateGoalToolInput, metadata, objective="second")
    assert result.is_error
    # With replace=True, it should succeed.
    result = await _run(
        tool, CreateGoalToolInput, metadata, objective="second", replace=True
    )
    assert not result.is_error


@pytest.mark.asyncio
async def test_create_goal_tool_without_goal_mode() -> None:
    tool = CreateGoalTool()
    # No goal_mode in metadata.
    result = await _run(tool, CreateGoalToolInput, {}, objective="X")
    assert result.is_error
    assert "not available" in result.output


# --------------------------------------------------------------- UpdateGoalTool


@pytest.mark.asyncio
async def test_update_goal_complete_sets_stop(metadata: dict, goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    tool = UpdateGoalTool()
    result = await _run(
        tool, UpdateGoalToolInput, metadata, status="complete", reason="done"
    )
    assert not result.is_error
    # The stop signal must be in metadata so post_tool_stage can halt the turn.
    assert result.metadata.get(GOAL_STOP_TURN_KEY) is True
    # The record still exists with status="complete"; the driver will clear it.
    snap = goal_mode.get_goal()
    assert snap is not None
    assert snap.status == "complete"


@pytest.mark.asyncio
async def test_update_goal_blocked_sets_stop(metadata: dict, goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    tool = UpdateGoalTool()
    result = await _run(
        tool, UpdateGoalToolInput, metadata, status="blocked", reason="stuck"
    )
    assert not result.is_error
    assert result.metadata.get(GOAL_STOP_TURN_KEY) is True
    assert goal_mode.get_goal().status == "blocked"


@pytest.mark.asyncio
async def test_update_goal_paused_sets_stop(metadata: dict, goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    tool = UpdateGoalTool()
    result = await _run(tool, UpdateGoalToolInput, metadata, status="paused")
    assert not result.is_error
    assert result.metadata.get(GOAL_STOP_TURN_KEY) is True
    assert goal_mode.get_goal().status == "paused"


@pytest.mark.asyncio
async def test_update_goal_active_no_stop(metadata: dict, goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing")
    goal_mode.pause_goal()
    tool = UpdateGoalTool()
    result = await _run(tool, UpdateGoalToolInput, metadata, status="active")
    assert not result.is_error
    # active = resume; driver keeps iterating, no stop signal.
    assert GOAL_STOP_TURN_KEY not in result.metadata
    assert goal_mode.get_goal().status == "active"


@pytest.mark.asyncio
async def test_update_goal_no_goal_errors(metadata: dict) -> None:
    # goal_mode exists but no goal has been created.
    GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    tool = UpdateGoalTool()
    result = await _run(
        tool, UpdateGoalToolInput, metadata, status="complete", reason="x"
    )
    assert result.is_error


# ----------------------------------------------------------------- GetGoalTool


@pytest.mark.asyncio
async def test_get_goal_tool_no_goal(metadata: dict, goal_mode: GoalMode) -> None:
    tool = GetGoalTool()
    result = await _run(tool, GetGoalToolInput, metadata)
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload == {"goal": None}


@pytest.mark.asyncio
async def test_get_goal_tool_with_goal(metadata: dict, goal_mode: GoalMode) -> None:
    goal_mode.create_goal("do thing", completion_criterion="all green")
    tool = GetGoalTool()
    result = await _run(tool, GetGoalToolInput, metadata)
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["goal"]["objective"] == "do thing"
    assert payload["goal"]["completion_criterion"] == "all green"
    assert payload["goal"]["status"] == "active"


@pytest.mark.asyncio
async def test_get_goal_tool_read_only(goal_mode: GoalMode, metadata: dict) -> None:
    tool = GetGoalTool()
    assert tool.is_read_only(GetGoalToolInput()) is True


# ---------------------------------------------------------- SetGoalBudgetTool


@pytest.mark.asyncio
async def test_set_goal_budget_turns_normalized(
    metadata: dict, goal_mode: GoalMode
) -> None:
    goal_mode.create_goal("do thing")
    tool = SetGoalBudgetTool()
    result = await _run(
        tool,
        SetGoalBudgetToolInput,
        metadata,
        value=3.7,
        unit="turns",
    )
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["goal"]["budget"]["turn_budget"] == 4


@pytest.mark.asyncio
async def test_set_goal_budget_time_valid(
    metadata: dict, goal_mode: GoalMode
) -> None:
    goal_mode.create_goal("do thing")
    tool = SetGoalBudgetTool()
    result = await _run(
        tool, SetGoalBudgetToolInput, metadata, value=5, unit="minutes"
    )
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["goal"]["budget"]["wall_clock_budget_ms"] == 5 * 60 * 1000


@pytest.mark.asyncio
async def test_set_goal_budget_time_unreasonable(
    metadata: dict, goal_mode: GoalMode
) -> None:
    goal_mode.create_goal("do thing")
    tool = SetGoalBudgetTool()
    result = await _run(
        tool, SetGoalBudgetToolInput, metadata, value=25, unit="hours"
    )
    assert result.is_error
    assert "not a reasonable" in result.output


@pytest.mark.asyncio
async def test_set_goal_budget_no_goal(metadata: dict, goal_mode: GoalMode) -> None:
    tool = SetGoalBudgetTool()
    result = await _run(tool, SetGoalBudgetToolInput, metadata, value=5, unit="turns")
    assert result.is_error


@pytest.mark.asyncio
async def test_tools_access_goal_mode_via_context_metadata(
    metadata: dict, goal_mode: GoalMode
) -> None:
    """Critical: tools must read GoalMode from context.metadata[GOAL_MODE_KEY],
    not from any engine attribute."""
    goal_mode.create_goal("Ship feature X")
    ctx = _context(metadata)
    assert ctx.metadata[GOAL_MODE_KEY] is goal_mode
    result = await GetGoalTool().execute(GetGoalToolInput(), ctx)
    payload = json.loads(result.output)
    assert payload["goal"]["objective"] == "Ship feature X"


# --------------------------------------------------------------- QueueGoalTool


@pytest.mark.asyncio
async def test_queue_goal_tool_enqueues(metadata: dict, goal_mode: GoalMode) -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore
    from openharness.tools.queue_goal_tool import QueueGoalTool, QueueGoalToolInput

    queue = GoalQueueStore(metadata)
    metadata[GOAL_QUEUE_KEY] = queue
    tool = QueueGoalTool()
    result = await _run(
        tool,
        QueueGoalToolInput,
        metadata,
        objective="Follow-up: write tests",
        priority=3,
    )
    assert not result.is_error
    payload = json.loads(result.output)
    assert payload["queue_length"] == 1
    assert payload["queued"]["objective"] == "Follow-up: write tests"
    assert payload["queued"]["priority"] == 3


@pytest.mark.asyncio
async def test_queue_goal_tool_builds_queue_on_the_fly(
    metadata: dict, goal_mode: GoalMode
) -> None:
    """If the runtime forgot to inject the queue, the tool builds one."""
    from openharness.goal.queue import GOAL_QUEUE_KEY
    from openharness.tools.queue_goal_tool import QueueGoalTool, QueueGoalToolInput

    tool = QueueGoalTool()
    result = await _run(
        tool, QueueGoalToolInput, metadata, objective="Follow-up"
    )
    assert not result.is_error
    assert GOAL_QUEUE_KEY in metadata
