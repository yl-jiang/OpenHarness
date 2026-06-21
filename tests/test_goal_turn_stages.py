"""Tests for goal_stop_turn detection in post_tool_stage (turn_stages.py)."""

from __future__ import annotations

import pytest

from openharness.engine.messages import (
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.engine.turn_stages import TurnAction, TurnState, post_tool_stage
from openharness.tools.update_goal_tool import GOAL_STOP_TURN_KEY


def _make_state() -> TurnState:
    """Build a minimal TurnState for post_tool_stage. context can be None
    because post_tool_stage only reads context.model/hook_executor (both
    guarded by the paths we exercise)."""

    class _FakeContext:
        model = "test"
        hook_executor = None
        tool_metadata = {}
        max_tokens = 1024

    state = TurnState(
        context=_FakeContext(),
        external_messages=[],
    )
    state.session_id = "test"
    return state


@pytest.mark.asyncio
async def test_post_tool_stage_goal_stop_turn_sets_stop() -> None:
    state = _make_state()
    # Simulate one tool call + its result carrying the stop signal.
    state.tool_calls = [ToolUseBlock(id="u1", name="update_goal", input={"status": "complete"})]
    state.tool_results = [
        ToolResultBlock(
            tool_use_id="u1",
            content="Goal marked complete",
            result_metadata={GOAL_STOP_TURN_KEY: True},
        )
    ]

    events = []
    async for event, _usage in post_tool_stage(state):
        events.append(event)

    assert state.action == TurnAction.STOP


@pytest.mark.asyncio
async def test_post_tool_stage_no_goal_stop_turn_keeps_action() -> None:
    state = _make_state()
    state.tool_calls = [ToolUseBlock(id="u1", name="bash", input={"command": "ls"})]
    state.tool_results = [
        ToolResultBlock(
            tool_use_id="u1",
            content="hello",
            result_metadata={},
        )
    ]

    async for _event, _usage in post_tool_stage(state):
        pass

    # No done(), no goal_stop_turn → action is left at PROCEED.
    assert state.action == TurnAction.PROCEED


@pytest.mark.asyncio
async def test_post_tool_stage_goal_stop_turn_among_many_results() -> None:
    """Parallel tool batch: one UpdateGoal(complete), other tools continue.
    The stop signal still halts the turn loop."""
    state = _make_state()
    state.tool_calls = [
        ToolUseBlock(id="u1", name="bash", input={"command": "ls"}),
        ToolUseBlock(id="u2", name="update_goal", input={"status": "complete"}),
    ]
    state.tool_results = [
        ToolResultBlock(
            tool_use_id="u1",
            content="file.txt",
            result_metadata={},
        ),
        ToolResultBlock(
            tool_use_id="u2",
            content="done",
            result_metadata={GOAL_STOP_TURN_KEY: True},
        ),
    ]

    async for _event, _usage in post_tool_stage(state):
        pass

    assert state.action == TurnAction.STOP
