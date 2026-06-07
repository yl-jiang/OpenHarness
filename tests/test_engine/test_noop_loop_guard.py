"""Tests for the split no-op loop guard.

The engine tracks no-op tool results (handler flagged ``metadata.noop=True``)
in a dedicated ``TOOL_NOOP_HISTORY`` slot so they never crowd out real-error
signals in the shared ``TOOL_CALL_HISTORY``. Blocking is more aggressive than
the error loop guard: a single prior identical no-op is enough to block the
next one (``NOOP_PRIOR_BLOCK_COUNT = 1``).
"""

from __future__ import annotations

from openharness.engine.messages import ToolResultBlock
from openharness.engine.tool_loop_guard import (
    record_tool_call_result,
    should_block_noop_call,
    should_block_tool_call,
)
from openharness.engine.types import ToolMetadataKey


def _noop_result(tool_use_id: str, content: str = "no-op") -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=tool_use_id,
        content=content,
        is_error=True,
        result_metadata={"noop": True},
    )


def _error_result(tool_use_id: str, content: str = "error") -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=tool_use_id,
        content=content,
        is_error=True,
    )


def test_should_block_noop_call_blocks_second_identical_noop() -> None:
    metadata: dict[str, object] = {}
    tool_input = {"record_id": "abc123", "summary": "same"}
    result = _noop_result("toolu_1")

    record_tool_call_result(metadata, "solo_update_record", tool_input, result)

    decision = should_block_noop_call(metadata, "solo_update_record", tool_input)
    assert decision.blocked is True
    assert "solo_update_record" in decision.reason


def test_should_block_noop_call_allows_first_noop() -> None:
    metadata: dict[str, object] = {}
    tool_input = {"record_id": "abc123", "summary": "same"}
    decision = should_block_noop_call(metadata, "solo_update_record", tool_input)
    assert decision.blocked is False


def test_should_block_noop_call_allows_different_inputs() -> None:
    metadata: dict[str, object] = {}
    result = _noop_result("toolu_1")

    record_tool_call_result(
        metadata, "solo_update_record", {"record_id": "abc", "summary": "x"}, result
    )

    # Different record_id — must not be blocked even with one prior noop in history.
    decision = should_block_noop_call(
        metadata, "solo_update_record", {"record_id": "def", "summary": "y"}
    )
    assert decision.blocked is False


def test_noop_history_is_separate_from_error_history() -> None:
    """No-op results must live in TOOL_NOOP_HISTORY, not TOOL_CALL_HISTORY, so
    a sequence of no-op updates cannot trigger the real-error doom-loop guard
    against a legitimate future call.
    """
    metadata: dict[str, object] = {}
    tool_input = {"record_id": "abc", "summary": "same"}
    result = _noop_result("toolu_1")

    for _ in range(5):
        record_tool_call_result(metadata, "solo_update_record", tool_input, result)

    noop_history = metadata.get(ToolMetadataKey.TOOL_NOOP_HISTORY.value)
    error_history = metadata.get(ToolMetadataKey.TOOL_CALL_HISTORY.value)
    assert isinstance(noop_history, list) and len(noop_history) == 5
    assert error_history in (None, [])

    # Real-error doom-loop guard must NOT fire on the noop-filled history.
    decision = should_block_tool_call(metadata, "solo_update_record", tool_input)
    assert decision.blocked is False


def test_noop_history_does_not_pollute_real_error_detection() -> None:
    """A real-error sequence still triggers should_block_tool_call even when
    many noop results have been accumulated in TOOL_NOOP_HISTORY first.
    """
    metadata: dict[str, object] = {}
    noop_input = {"record_id": "abc", "summary": "same"}
    for _ in range(5):
        record_tool_call_result(
            metadata,
            "solo_update_record",
            noop_input,
            _noop_result("toolu_noop"),
        )

    # Now produce 3 real identical errors on a different tool.
    error_input = {"pattern": "missing"}
    for _ in range(3):
        record_tool_call_result(
            metadata, "grep", error_input, _error_result("toolu_err")
        )

    decision = should_block_tool_call(metadata, "grep", error_input)
    assert decision.blocked is True
    assert "grep" in decision.reason


def test_noop_history_is_capped_at_max_entries() -> None:
    """TOOL_NOOP_HISTORY must be capped so an abusive loop cannot grow it
    unbounded.
    """
    metadata: dict[str, object] = {}
    tool_input = {"record_id": "abc", "summary": "same"}
    result = _noop_result("toolu_1")

    # Push well past MAX_NOOP_HISTORY (=12); the list must stay bounded.
    for i in range(30):
        record_tool_call_result(
            metadata, "solo_update_record", {**tool_input, "i": i}, result
        )

    history = metadata.get(ToolMetadataKey.TOOL_NOOP_HISTORY.value)
    assert isinstance(history, list)
    assert len(history) <= 12


def test_noop_does_not_route_through_real_error_history() -> None:
    """A handler-flagged noop with is_error=True must NOT count toward the
    real-error should_block_tool_call threshold — even when three such noop
    results are recorded, should_block_tool_call must still return False.
    """
    metadata: dict[str, object] = {}
    tool_input = {"record_id": "abc", "summary": "same"}
    for _ in range(3):
        record_tool_call_result(
            metadata, "solo_update_record", tool_input, _noop_result("toolu_noop")
        )

    # should_block_tool_call uses TOOL_CALL_HISTORY only — noop results must
    # not be there, so this must never block.
    decision = should_block_tool_call(metadata, "solo_update_record", tool_input)
    assert decision.blocked is False
