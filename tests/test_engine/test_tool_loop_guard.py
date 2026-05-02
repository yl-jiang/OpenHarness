from __future__ import annotations

from openharness.engine.messages import ToolResultBlock
from openharness.engine.tool_loop_guard import (
    build_doom_loop_result,
    record_tool_call_result,
    should_block_tool_call,
)


def test_doom_loop_guard_blocks_after_repeated_identical_failures() -> None:
    metadata: dict[str, object] = {}
    tool_input = {"pattern": "missing"}
    result = ToolResultBlock(tool_use_id="toolu_1", content="no matches", is_error=True)

    for _ in range(3):
        record_tool_call_result(metadata, "grep", tool_input, result)

    decision = should_block_tool_call(metadata, "grep", tool_input)

    assert decision.blocked is True
    assert "grep" in decision.reason
    assert "3 consecutive identical failing calls" in decision.reason


def test_doom_loop_guard_ignores_different_inputs_and_successes() -> None:
    metadata: dict[str, object] = {}
    failure = ToolResultBlock(tool_use_id="toolu_1", content="no matches", is_error=True)
    success = ToolResultBlock(tool_use_id="toolu_2", content="ok", is_error=False)

    record_tool_call_result(metadata, "grep", {"pattern": "a"}, failure)
    record_tool_call_result(metadata, "grep", {"pattern": "b"}, failure)
    record_tool_call_result(metadata, "grep", {"pattern": "a"}, success)

    decision = should_block_tool_call(metadata, "grep", {"pattern": "a"})

    assert decision.blocked is False


def test_build_doom_loop_result_is_actionable() -> None:
    result = build_doom_loop_result(
        tool_use_id="toolu_loop",
        tool_name="grep",
        reason="Detected 3 consecutive identical failing calls to grep.",
    )

    assert result.is_error is True
    assert "Detected 3 consecutive identical failing calls to grep." in result.content
    assert "Try a different approach" in result.content
