"""Tests for QueryEngine._drive_goal (multi-turn goal driver)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import (
    ConversationMessage,
    TextBlock,
    ToolUseBlock,
)
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    AssistantTurnComplete,
    GoalUpdatedEvent,
)
from openharness.config.settings import PermissionSettings
from openharness.goal.state import GOAL_MODE_KEY, GoalBudgetLimits, GoalMode
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.tools.base import ToolRegistry
from openharness.tools.create_goal_tool import CreateGoalTool
from openharness.tools.get_goal_tool import GetGoalTool
from openharness.tools.set_goal_budget_tool import SetGoalBudgetTool
from openharness.tools.update_goal_tool import UpdateGoalTool


# -------------------------------------------------------------------- fixtures


@dataclass
class _ScriptedResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class _ScriptedApiClient:
    """API client that replays a fixed list of responses."""

    def __init__(self, responses: list[_ScriptedResponse]) -> None:
        self._responses = list(responses)
        self.call_count = 0

    async def stream_message(self, request):
        self.call_count += 1
        from openharness.api.client import ApiMessageCompleteEvent, ApiTextDeltaEvent

        response = self._responses.pop(0)
        for block in response.message.content:
            if isinstance(block, TextBlock) and block.text:
                yield ApiTextDeltaEvent(text=block.text)
        yield ApiMessageCompleteEvent(
            message=response.message,
            usage=response.usage,
            stop_reason=None,
        )


def _tool_registry() -> ToolRegistry:
    """Goal-tool-only registry (no Bash etc.) — keeps driver tests fast."""
    registry = ToolRegistry()
    registry.register(CreateGoalTool())
    registry.register(UpdateGoalTool())
    registry.register(GetGoalTool())
    registry.register(SetGoalBudgetTool())
    return registry


def _engine(
    tmp_path: Path,
    responses: list[_ScriptedResponse],
    *,
    tool_metadata: dict | None = None,
) -> QueryEngine:
    metadata = tool_metadata if tool_metadata is not None else {}
    goal_mode = GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = goal_mode
    return QueryEngine(
        api_client=_ScriptedApiClient(responses),
        tool_registry=_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="test",
        system_prompt="system",
        max_turns=8,
        tool_metadata=metadata,
    )


def _usage(**overrides) -> UsageSnapshot:
    defaults = dict(input_tokens=10, output_tokens=10)
    defaults.update(overrides)
    return UsageSnapshot(**defaults)


def _text_reply(text: str) -> _ScriptedResponse:
    return _ScriptedResponse(
        message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
        usage=_usage(),
    )


def _tool_call_response(tool_name: str, tool_input: dict) -> _ScriptedResponse:
    return _ScriptedResponse(
        message=ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id=f"u_{tool_name}", name=tool_name, input=tool_input)],
        ),
        usage=_usage(),
    )


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_drive_goal_single_turn_complete(tmp_path: Path) -> None:
    """Model calls UpdateGoal(complete) on turn 1; driver runs a final turn
    for the completion summary and emits a completion GoalUpdatedEvent."""
    responses = [
        _tool_call_response("update_goal", {"status": "complete", "reason": "done"}),
        _text_reply("All work is done."),
    ]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature X"):
        events.append(event)

    # At least one GoalUpdatedEvent, ending with snapshot=None (cleared).
    goal_events = [e for e in events if isinstance(e, GoalUpdatedEvent)]
    assert goal_events, "Expected at least one GoalUpdatedEvent"
    assert goal_events[-1].snapshot is None
    assert goal_events[-2].change is not None
    assert goal_events[-2].change.kind == "completion"
    assert goal_events[-2].change.status == "complete"
    # Goal must be fully cleared after the run.
    assert goal_mode.get_goal() is None


@pytest.mark.asyncio
async def test_drive_goal_budget_exhaustion(tmp_path: Path) -> None:
    """With turn_budget=1 the second iteration hits the budget pre-check
    and the driver blocks the goal without calling the API."""
    responses = [
        _text_reply("starting work"),
        # A second response is available, but the driver must not use it.
        _text_reply("should not be used"),
    ]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")
    goal_mode.set_budget_limits(GoalBudgetLimits(turn_budget=1))

    events = []
    async for event in engine.submit_message("Ship feature X"):
        events.append(event)

    goal_events = [e for e in events if isinstance(e, GoalUpdatedEvent)]
    last_change = next(
        (e.change for e in reversed(goal_events) if e.change is not None),
        None,
    )
    assert last_change is not None
    assert last_change.status == "blocked"
    assert "budget" in (last_change.reason or "")
    # Only the first turn's API call was made.
    api_client = engine._api_client  # type: ignore[attr-defined]
    assert api_client.call_count == 1


@pytest.mark.asyncio
async def test_drive_goal_token_stats_from_turn_complete(tmp_path: Path) -> None:
    """Token counts from AssistantTurnComplete.usage are accumulated into the
    goal snapshot."""
    responses = [
        _ScriptedResponse(
            message=ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(id="u1", name="update_goal", input={"status": "complete"})],
            ),
            usage=UsageSnapshot(input_tokens=50, output_tokens=75, total_tokens=125),
        ),
        _text_reply("Summary."),
    ]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature X"):
        events.append(event)

    # The completion GoalUpdatedEvent carries the final stats.
    goal_events = [e for e in events if isinstance(e, GoalUpdatedEvent)]
    completion_event = next(
        (e for e in goal_events if e.change is not None and e.change.kind == "completion"),
        None,
    )
    assert completion_event is not None
    assert completion_event.change.stats is not None
    assert completion_event.change.stats.tokens_used >= 125


@pytest.mark.asyncio
async def test_drive_goal_paused_by_model(tmp_path: Path) -> None:
    """Model calls UpdateGoal(paused); the driver emits a lifecycle event
    and exits without injecting a completion summary turn."""
    responses = [
        _tool_call_response("update_goal", {"status": "paused", "reason": "need input"}),
        # If the driver incorrectly continues, it would consume this reply.
        _text_reply("should not be used"),
    ]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature X"):
        events.append(event)

    # Only the UpdateGoal API call happened.
    api_client = engine._api_client  # type: ignore[attr-defined]
    assert api_client.call_count == 1
    # Goal is paused, not cleared.
    snapshot = goal_mode.get_goal()
    assert snapshot is not None
    assert snapshot.status == "paused"


@pytest.mark.asyncio
async def test_no_goal_routes_to_regular_turn(tmp_path: Path) -> None:
    """Without an active goal, submit_message runs the normal turn pipeline."""
    responses = [_text_reply("hello")]
    engine = _engine(tmp_path, responses)
    events = []
    async for event in engine.submit_message("hi"):
        events.append(event)
    goal_events = [e for e in events if isinstance(e, GoalUpdatedEvent)]
    assert not goal_events, "No GoalUpdatedEvent expected when no goal is set"
    complete_events = [e for e in events if isinstance(e, AssistantTurnComplete)]
    assert complete_events


@pytest.mark.asyncio
async def test_goal_state_persists_across_turn_rollback(tmp_path: Path) -> None:
    """Goal state must NOT be cleared when the turn checkpoint rolls back
    turn-scoped metadata (goal_state is not in turn_checkpoint_keys)."""
    from openharness.engine.types import ToolMetadataKey

    responses = [_text_reply("hello")]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")
    goal_mode.increment_turn()

    # Capture + restore a turn checkpoint — simulates user Ctrl+C.
    checkpoint = engine.capture_turn_checkpoint()
    goal_mode.increment_turn()
    goal_mode.increment_turn()
    assert goal_mode.get_goal().turns_used == 3

    engine.restore_turn_checkpoint(checkpoint)

    # Goal state survives the rollback because "goal_state" is not in
    # turn_checkpoint_keys.
    snapshot = goal_mode.get_goal()
    assert snapshot is not None
    assert snapshot.turns_used == 3
    assert ToolMetadataKey.GOAL_STATE not in ToolMetadataKey.turn_checkpoint_keys()


@pytest.mark.asyncio
async def test_goal_reminder_injected_first_turn(tmp_path: Path) -> None:
    """On the first goal turn, a reminder is injected into the conversation
    (merged with the user's trailing message)."""
    responses = [_text_reply("ok")]
    engine = _engine(tmp_path, responses)
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Ship feature X", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature X"):
        events.append(event)

    # Find a user message that contains the reminder marker.
    user_texts = [m.text for m in engine.messages if m.role == "user"]
    assert any("active goal" in text for text in user_texts), (
        "Expected a goal reminder in the conversation history"
    )
