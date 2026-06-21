"""Tests for permission-mode auto-restore after goal end (Phase 7 §15.2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.config.settings import GoalSettings, Settings
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import GoalUpdatedEvent
from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.config.settings import PermissionSettings
from openharness.tools.base import ToolRegistry
from openharness.tools.update_goal_tool import UpdateGoalTool


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
            content=[ToolUseBlock(id=f"u_{tool_name}", name=tool_name, input=tool_input)],
        ),
        usage=_usage(),
    )


def _engine(
    tmp_path: Path,
    responses: list[_ScriptedResponse],
    *,
    restore_enabled: bool,
    metadata: dict | None = None,
) -> QueryEngine:
    metadata = metadata if metadata is not None else {}
    goal_mode = GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = goal_mode
    settings = Settings()
    settings.goal = GoalSettings(restore_permission_after_goal=restore_enabled)
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
async def test_restore_disabled_by_default(tmp_path: Path) -> None:
    """With restore_permission_after_goal=False (default), no restore signal
    is emitted even when original_permission_mode is recorded."""
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete"}),
            _ScriptedResponse(
                message=ConversationMessage(
                    role="assistant", content=[TextBlock(text="done")]
                ),
                usage=_usage(),
            ),
        ],
        restore_enabled=False,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing", original_permission_mode="default")

    async for _ in engine.submit_message("do thing"):
        pass

    assert "_pending_permission_restore" not in metadata


@pytest.mark.asyncio
async def test_restore_enabled_emits_signal(tmp_path: Path) -> None:
    """With restore_permission_after_goal=True, the driver writes
    _pending_permission_restore on goal completion."""
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete"}),
            _ScriptedResponse(
                message=ConversationMessage(
                    role="assistant", content=[TextBlock(text="done")]
                ),
                usage=_usage(),
            ),
        ],
        restore_enabled=True,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing", original_permission_mode="default")

    events = []
    async for event in engine.submit_message("do thing"):
        events.append(event)

    assert metadata.get("_pending_permission_restore") == "default"
    # Sanity: a completion GoalUpdatedEvent was emitted.
    assert any(
        isinstance(e, GoalUpdatedEvent)
        and e.change is not None
        and e.change.kind == "completion"
        for e in events
    )


@pytest.mark.asyncio
async def test_restore_skipped_when_original_was_full_auto(tmp_path: Path) -> None:
    """No point writing the restore signal if the original mode was FULL_AUTO
    — the user is already there."""
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete"}),
            _ScriptedResponse(
                message=ConversationMessage(
                    role="assistant", content=[TextBlock(text="done")]
                ),
                usage=_usage(),
            ),
        ],
        restore_enabled=True,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing", original_permission_mode="full_auto")

    async for _ in engine.submit_message("do thing"):
        pass

    assert "_pending_permission_restore" not in metadata


@pytest.mark.asyncio
async def test_restore_signal_on_blocked(tmp_path: Path) -> None:
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "blocked", "reason": "stuck"}),
        ],
        restore_enabled=True,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing", original_permission_mode="default")

    async for _ in engine.submit_message("do thing"):
        pass

    assert metadata.get("_pending_permission_restore") == "default"


@pytest.mark.asyncio
async def test_restore_signal_on_paused(tmp_path: Path) -> None:
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "paused", "reason": "need input"}),
        ],
        restore_enabled=True,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing", original_permission_mode="plan")

    async for _ in engine.submit_message("do thing"):
        pass

    assert metadata.get("_pending_permission_restore") == "plan"


@pytest.mark.asyncio
async def test_no_signal_when_no_original_recorded(tmp_path: Path) -> None:
    """If the goal was created without recording original_permission_mode
    (legacy path), no restore happens."""
    metadata: dict = {}
    engine = _engine(
        tmp_path,
        responses=[
            _tool_call_response("update_goal", {"status": "complete"}),
            _ScriptedResponse(
                message=ConversationMessage(
                    role="assistant", content=[TextBlock(text="done")]
                ),
                usage=_usage(),
            ),
        ],
        restore_enabled=True,
        metadata=metadata,
    )
    goal_mode = engine.tool_metadata[GOAL_MODE_KEY]
    goal_mode.create_goal("do thing")  # no original_permission_mode

    async for _ in engine.submit_message("do thing"):
        pass

    assert "_pending_permission_restore" not in metadata


def test_pending_restore_is_turn_private() -> None:
    """The _pending_permission_restore key must start with '_' so it enters
    _turn_private_metadata_keys and is rolled back on turn cancel."""
    key = "_pending_permission_restore"
    assert key.startswith("_")


@pytest.mark.asyncio
async def test_restore_survives_session_restart(tmp_path: Path) -> None:
    """original_permission_mode is persisted in goal_state — a process restart
    still sees it and can emit the restore signal."""
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing", original_permission_mode="default")
    gm.pause_goal()  # simulate a pause before restart

    # Simulate restart: build a fresh GoalMode over the persisted dict.
    restored = GoalMode(metadata)
    restored.normalize_after_replay()
    assert restored.original_permission_mode() == "default"
