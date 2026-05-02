"""Helpers for repairing model-emitted tool names."""

from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal

from openharness.engine.messages import ToolResultBlock
from openharness.tools.base import ToolRegistry

RepairReason = Literal["exact", "case_insensitive", "alias", "fuzzy", "unknown"]

_FUZZY_REPAIR_THRESHOLD = 0.84
_FUZZY_AMBIGUITY_GAP = 0.25
_SUGGESTION_THRESHOLD = 0.55

_TOOL_NAME_ALIASES: dict[str, str] = {
    "ask_user": "ask_user_question",
    "edit": "edit_file",
    "fetch": "web_fetch",
    "find": "glob",
    "list": "glob",
    "ls": "glob",
    "read": "read_file",
    "search": "grep",
    "shell": "bash",
    "write": "write_file",
}


@dataclass(frozen=True)
class ToolNameRepair:
    """Result of resolving or repairing a requested tool name."""

    requested_name: str
    resolved_name: str | None
    reason: RepairReason
    suggestions: tuple[str, ...] = ()
    available_names: tuple[str, ...] = ()

    @property
    def repaired(self) -> bool:
        return self.resolved_name is not None and self.resolved_name != self.requested_name


def repair_tool_name(requested_name: str, registry: ToolRegistry) -> ToolNameRepair:
    """Resolve a requested tool name, repairing safe common mistakes."""

    available_names = _available_tool_names(registry)
    if requested_name in available_names:
        return ToolNameRepair(
            requested_name=requested_name,
            resolved_name=requested_name,
            reason="exact",
            available_names=available_names,
        )

    normalized = requested_name.lower()
    case_match = _single_case_insensitive_match(normalized, available_names)
    if case_match is not None:
        return ToolNameRepair(
            requested_name=requested_name,
            resolved_name=case_match,
            reason="case_insensitive",
            available_names=available_names,
        )

    alias_target = _TOOL_NAME_ALIASES.get(normalized)
    if alias_target in available_names:
        return ToolNameRepair(
            requested_name=requested_name,
            resolved_name=alias_target,
            reason="alias",
            available_names=available_names,
        )

    ranked = _rank_tool_names(requested_name, available_names)
    suggestions = tuple(name for name, score in ranked if score >= _SUGGESTION_THRESHOLD)
    if ranked and ranked[0][1] >= _FUZZY_REPAIR_THRESHOLD:
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        if ranked[0][1] - second_score >= _FUZZY_AMBIGUITY_GAP:
            return ToolNameRepair(
                requested_name=requested_name,
                resolved_name=ranked[0][0],
                reason="fuzzy",
                suggestions=suggestions,
                available_names=available_names,
            )

    return ToolNameRepair(
        requested_name=requested_name,
        resolved_name=None,
        reason="unknown",
        suggestions=suggestions,
        available_names=available_names,
    )


def build_invalid_tool_result(
    *,
    tool_use_id: str,
    requested_name: str,
    available_names: tuple[str, ...],
    suggestions: tuple[str, ...] = (),
) -> ToolResultBlock:
    """Build a structured tool_result for an invalid tool call."""

    payload = {
        "error_type": "invalid_tool",
        "requested_tool": requested_name,
        "message": "Use one of the available tool names exactly, or choose a suggested tool.",
        "suggestions": list(suggestions),
        "available_tools": sorted(available_names),
    }
    return ToolResultBlock(
        tool_use_id=tool_use_id,
        content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        is_error=True,
    )


def _available_tool_names(registry: ToolRegistry) -> tuple[str, ...]:
    return tuple(sorted(tool.name for tool in registry.list_tools()))


def _single_case_insensitive_match(normalized_name: str, available_names: tuple[str, ...]) -> str | None:
    matches = [name for name in available_names if name.lower() == normalized_name]
    if len(matches) == 1:
        return matches[0]
    return None


def _rank_tool_names(requested_name: str, available_names: tuple[str, ...]) -> list[tuple[str, float]]:
    ranked = [
        (name, SequenceMatcher(a=requested_name.lower(), b=name.lower()).ratio())
        for name in available_names
    ]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return ranked
