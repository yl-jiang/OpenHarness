"""Tests for the driver's queue advancement logic (Phase 6)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.config.settings import GoalSettings, Settings
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import GoalUpdatedEvent
from openharness.goal.queue import GoalQueueStore
from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.config.settings import PermissionSettings
from openharness.tools.base import ToolRegistry
from openharness.tools.update_goal_tool import UpdateGoalTool

# Runtime handle lives at "goal_queue"; GOAL_QUEUE_KEY ("goal_queue_state")
# is the persistence key (holds a serialized list after each mutation).
QUEUE_RUNTIME_KEY = "goal_queue"


# -------------------------------------------------------------------- fixtures


@dataclass
class _ScriptedResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class _ScriptedApiClient:
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
    registry = ToolRegistry()
    registry.register(UpdateGoalTool())
    return registry


def _usage(**overrides) -> UsageSnapshot:
    defaults = dict(input_tokens=10, output_tokens=10)
    defaults.update(overrides)
    return UsageSnapshot(**defaults)


def _tool_call_response(tool_name: str, tool_input: dict) -> _ScriptedResponse:
    return _ScriptedResponse(
        message=ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id=f"u_{tool_name}_{id(tool_input)}", name=tool_name, input=tool_input)],
        ),
        usage=_usage(),
    )


def _text_reply(text: str) -> _ScriptedResponse:
    return _ScriptedResponse(
        message=ConversationMessage(role="assistant", content=[TextBlock(text=text)]),
        usage=_usage(),
    )


def _engine(
    tmp_path: Path,
    responses: list[_ScriptedResponse],
    *,
    auto_advance_on_blocked: bool = False,
    metadata: dict | None = None,
) -> QueryEngine:
    metadata = metadata if metadata is not None else {}
    goal_mode = GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = goal_mode
    queue = GoalQueueStore(metadata)
    metadata[QUEUE_RUNTIME_KEY] = queue
    settings = Settings()
    settings.goal = GoalSettings(auto_advance_on_blocked=auto_advance_on_blocked)
    return QueryEngine(
        api_client=_ScriptedApiClient(responses),
        tool_registry=_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(mode=PermissionMode.FULL_AUTO)
        ),
        cwd=tmp_path,
        model="test",
        system_prompt="system",
        max_turns=8,
        tool_metadata=metadata,
        settings=settings,
    )


# --------------------------------------------------------------------- tests


@pytest.mark.asyncio
async def test_driver_auto_starts_next_after_complete(tmp_path: Path) -> None:
    """Goal A completes → driver pops Goal B from the queue and starts it."""
    engine = _engine(
        tmp_path,
        responses=[
            # Goal A: model calls UpdateGoal(complete).
            _tool_call_response("update_goal", {"status": "complete", "reason": "done"}),
            # Goal A summary turn.
            _text_reply("A done."),
            # Goal B starts: model replies to the promoted objective.
            _text_reply("starting B"),
            # Goal B: model completes immediately.
            _tool_call_response("update_goal", {"status": "complete", "reason": "also done"}),
            # Goal B summary turn.
            _text_reply("B done."),
        ],
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    queue = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    queue.enqueue("Goal B")
    goal_mode.create_goal("Goal A", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature A"):
        events.append(event)

    # Queue must be empty: Goal B was promoted and then completed.
    queue_after = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    assert len(queue_after) == 0
    # Two completion events (one per goal).
    completion_events = [
        e for e in events
        if isinstance(e, GoalUpdatedEvent)
        and e.change is not None
        and e.change.kind == "completion"
    ]
    assert len(completion_events) == 2
    # At least 4 API calls (A turn, A summary, B turn, B summary).
    assert engine._api_client.call_count >= 4


@pytest.mark.asyncio
async def test_driver_does_not_advance_after_blocked(tmp_path: Path) -> None:
    """Default behavior: blocked goal does NOT promote the next queue item."""
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "blocked", "reason": "stuck"}),
            # The next response must NOT be consumed.
            _text_reply("this should not run"),
        ],
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    queue = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    queue.enqueue("Goal B")
    goal_mode.create_goal("Goal A", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature A"):
        events.append(event)

    # Queue must still hold Goal B.
    queue_after = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    assert len(queue_after) == 1
    # Only one API call (Goal A's blocked turn).
    assert engine._api_client.call_count == 1


@pytest.mark.asyncio
async def test_driver_advances_after_blocked_when_opted_in(tmp_path: Path) -> None:
    """auto_advance_on_blocked=True → blocked goal promotes the queue."""
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "blocked", "reason": "stuck"}),
            _text_reply("starting B"),
            _tool_call_response("update_goal", {"status": "complete"}),
            _text_reply("B done."),
        ],
        auto_advance_on_blocked=True,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    queue = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    queue.enqueue("Goal B")
    goal_mode.create_goal("Goal A", actor="user")

    async for _ in engine.submit_message("Ship feature A"):
        pass

    queue_after = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    assert len(queue_after) == 0  # Goal B was promoted and completed


@pytest.mark.asyncio
async def test_driver_does_not_advance_after_cancel(tmp_path: Path) -> None:
    """User cancel does not promote the queue (explicit human intervention)."""
    engine = _engine(
        tmp_path,
        responses=[
            # Model pauses — queue must remain intact.
            _tool_call_response("update_goal", {"status": "paused"}),
        ],
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    queue = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    queue.enqueue("Goal B")
    goal_mode.create_goal("Goal A", actor="user")

    async for _ in engine.submit_message("Ship feature A"):
        pass

    # After pause the queue is still intact.
    queue_after = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    assert len(queue_after) == 1


@pytest.mark.asyncio
async def test_driver_empty_queue_just_exits(tmp_path: Path) -> None:
    """Complete with an empty queue → driver exits cleanly."""
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete", "reason": "done"}),
            _text_reply("Summary."),
        ],
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("Goal A", actor="user")

    events = []
    async for event in engine.submit_message("Ship feature A"):
        events.append(event)

    # One completion event, then graceful exit.
    completion_events = [
        e for e in events
        if isinstance(e, GoalUpdatedEvent)
        and e.change is not None
        and e.change.kind == "completion"
    ]
    assert len(completion_events) == 1


@pytest.mark.asyncio
async def test_queued_goal_sees_previous_completion_summary(tmp_path: Path) -> None:
    """After Goal A completes, its summary message is in self._messages;
    Goal B's turn must see it (shared context)."""
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete", "reason": "done"}),
            _text_reply("Goal A summary text XYZ123"),
            _text_reply("Goal B reply"),
            _tool_call_response("update_goal", {"status": "complete"}),
            _text_reply("Goal B summary"),
        ],
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    queue = engine.tool_metadata[QUEUE_RUNTIME_KEY]
    queue.enqueue("Goal B")
    goal_mode.create_goal("Goal A", actor="user")

    async for _ in engine.submit_message("Ship feature A"):
        pass

    # After both goals complete, the conversation history must contain
    # Goal A's summary as an assistant message (the API returned it as a
    # text reply to the completion-summary user prompt).
    all_texts = [m.text for m in engine.messages]
    assert any("Goal A summary text XYZ123" in t for t in all_texts)
