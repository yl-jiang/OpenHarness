"""Guard against repeated identical failing tool calls."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from openharness.engine.messages import ToolResultBlock
from openharness.engine.types import ToolMetadataKey

DEFAULT_DOOM_LOOP_THRESHOLD = 3
MAX_TOOL_CALL_HISTORY = 12


@dataclass(frozen=True)
class DoomLoopDecision:
    blocked: bool
    reason: str = ""


def should_block_tool_call(
    tool_metadata: dict[str, object] | None,
    tool_name: str,
    tool_input: dict[str, object],
    *,
    threshold: int = DEFAULT_DOOM_LOOP_THRESHOLD,
) -> DoomLoopDecision:
    """Return whether a repeated failing call should be blocked before execution."""

    if threshold <= 0 or not isinstance(tool_metadata, dict):
        return DoomLoopDecision(blocked=False)
    history = tool_metadata.get(ToolMetadataKey.TOOL_CALL_HISTORY.value)
    if not isinstance(history, list) or len(history) < threshold:
        return DoomLoopDecision(blocked=False)

    input_hash = _stable_hash(tool_input)
    recent = history[-threshold:]
    if not all(isinstance(entry, dict) for entry in recent):
        return DoomLoopDecision(blocked=False)
    if not all(
        entry.get("tool_name") == tool_name
        and entry.get("input_hash") == input_hash
        and entry.get("is_error") is True
        for entry in recent
    ):
        return DoomLoopDecision(blocked=False)

    result_hashes = {entry.get("result_hash") for entry in recent}
    if len(result_hashes) != 1:
        return DoomLoopDecision(blocked=False)

    return DoomLoopDecision(
        blocked=True,
        reason=f"Detected {threshold} consecutive identical failing calls to {tool_name}.",
    )


def build_doom_loop_result(*, tool_use_id: str, tool_name: str, reason: str) -> ToolResultBlock:
    return ToolResultBlock(
        tool_use_id=tool_use_id,
        content=(
            f"{reason} Try a different approach, change the tool arguments, "
            "or ask the user for clarification before retrying."
        ),
        is_error=True,
    )


def record_tool_call_result(
    tool_metadata: dict[str, object] | None,
    tool_name: str,
    tool_input: dict[str, object],
    result: ToolResultBlock,
    *,
    max_entries: int = MAX_TOOL_CALL_HISTORY,
) -> None:
    """Record a compact signature for future doom-loop detection."""

    if not isinstance(tool_metadata, dict):
        return
    history = tool_metadata.setdefault(ToolMetadataKey.TOOL_CALL_HISTORY.value, [])
    if not isinstance(history, list):
        history = []
        tool_metadata[ToolMetadataKey.TOOL_CALL_HISTORY.value] = history
    history.append(
        {
            "tool_name": tool_name,
            "input_hash": _stable_hash(tool_input),
            "is_error": result.is_error,
            "result_hash": _stable_hash(result.content),
        }
    )
    if len(history) > max_entries:
        del history[:-max_entries]


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
