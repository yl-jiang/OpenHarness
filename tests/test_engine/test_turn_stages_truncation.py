"""Tests for truncation recovery in turn stages."""

from __future__ import annotations

import pytest

from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.stream_events import StatusEvent
from openharness.engine.turn_stages import (
    TurnAction,
    TurnState,
    _TRUNCATION_RETRY_TOKEN_MULTIPLIER,
    _MAX_EFFECTIVE_TOKENS_CAP,
    post_tool_stage,
    response_routing_stage,
)
from openharness.engine.messages import ToolResultBlock


class _MinimalContext:
    """Minimal stand-in for QueryContext."""

    def __init__(self) -> None:
        self.model = "test-model"
        self.system_prompt = ""
        self.require_explicit_done = False
        self.hook_executor = None
        self.tool_metadata: dict = {}


def _make_state(**overrides) -> TurnState:
    ctx = _MinimalContext()
    state = TurnState(
        context=ctx,
        external_messages=[],
        messages=[],
        effective_max_tokens=overrides.pop("effective_max_tokens", 8192),
    )
    for k, v in overrides.items():
        setattr(state, k, v)
    return state


class TestResponseRoutingTruncationRecovery:
    """When ALL tool calls are truncated, response_routing should retry."""

    @pytest.mark.asyncio
    async def test_all_truncated_bumps_tokens_and_retries(self):
        msg = ConversationMessage(role="assistant", content=[TextBlock(text="Let me write.")])
        state = _make_state(
            final_message=msg,
            stop_reason="length",
            truncated_tool_calls=2,
            effective_max_tokens=8192,
        )

        events = [ev async for ev in response_routing_stage(state)]

        assert state.action == TurnAction.NEXT_TURN
        assert state.effective_max_tokens == min(
            int(8192 * _TRUNCATION_RETRY_TOKEN_MULTIPLIER),
            _MAX_EFFECTIVE_TOKENS_CAP,
        )
        # A truncation notice is injected into messages
        last_msg = state.messages[-1]
        assert "truncation-recovery" in last_msg.text
        # A StatusEvent is yielded
        status_events = [e for e, _ in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 1
        assert "dropped" in status_events[0].message

    @pytest.mark.asyncio
    async def test_no_truncation_proceeds_normally(self):
        msg = ConversationMessage(role="assistant", content=[
            TextBlock(text="Done."),
        ])
        state = _make_state(
            final_message=msg,
            stop_reason="stop",
            truncated_tool_calls=0,
        )

        _ = [ev async for ev in response_routing_stage(state)]

        # No truncation → should stop (no tool uses, no done requirement)
        assert state.action == TurnAction.STOP

    @pytest.mark.asyncio
    async def test_max_tokens_capped(self):
        msg = ConversationMessage(role="assistant", content=[TextBlock(text="")])
        state = _make_state(
            final_message=msg,
            stop_reason="length",
            truncated_tool_calls=1,
            effective_max_tokens=100_000,
        )

        _ = [ev async for ev in response_routing_stage(state)]

        assert state.effective_max_tokens == _MAX_EFFECTIVE_TOKENS_CAP


class TestPostToolTruncationRecovery:
    """When SOME tool calls are truncated, post_tool should inject notice."""

    @pytest.mark.asyncio
    async def test_partial_truncation_injects_notice(self):
        state = _make_state(
            effective_max_tokens=4096,
            truncated_tool_calls=1,
            tool_calls=[ToolUseBlock(id="c1", name="read_file", input={"path": "/x"})],
            tool_results=[ToolResultBlock(tool_use_id="c1", content="ok", is_error=False)],
        )

        events = [ev async for ev in post_tool_stage(state)]

        assert state.effective_max_tokens == min(
            int(4096 * _TRUNCATION_RETRY_TOKEN_MULTIPLIER),
            _MAX_EFFECTIVE_TOKENS_CAP,
        )
        # Truncation notice injected
        notice_msgs = [m for m in state.messages if "truncation-recovery" in m.text]
        assert len(notice_msgs) == 1
        # StatusEvent yielded
        status_events = [e for e, _ in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 1

    @pytest.mark.asyncio
    async def test_no_truncation_no_notice(self):
        state = _make_state(
            effective_max_tokens=4096,
            truncated_tool_calls=0,
            tool_calls=[ToolUseBlock(id="c1", name="read_file", input={"path": "/x"})],
            tool_results=[ToolResultBlock(tool_use_id="c1", content="ok", is_error=False)],
        )

        events = [ev async for ev in post_tool_stage(state)]

        assert state.effective_max_tokens == 4096
        notice_msgs = [m for m in state.messages if "truncation-recovery" in m.text]
        assert len(notice_msgs) == 0
        status_events = [e for e, _ in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 0
