from __future__ import annotations

import json

from pydantic import BaseModel

from openharness.engine.tool_repair import build_invalid_tool_result, repair_tool_name
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult


class _MarkerInput(BaseModel):
    value: str = ""


class _MarkerTool(BaseTool):
    name = "marker_tool"
    description = "Marker test tool"
    input_model = _MarkerInput

    async def execute(self, arguments: _MarkerInput, context: ToolExecutionContext) -> ToolResult:
        del context
        return ToolResult(output=arguments.value)


def _registry(*tools: BaseTool) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


def test_repair_tool_name_resolves_case_mismatch() -> None:
    result = repair_tool_name("MARKER_TOOL", _registry(_MarkerTool()))

    assert result.resolved_name == "marker_tool"
    assert result.reason == "case_insensitive"


def test_repair_tool_name_resolves_known_alias_when_available() -> None:
    class ReadFileTool(_MarkerTool):
        name = "read_file"

    result = repair_tool_name("read", _registry(ReadFileTool()))

    assert result.resolved_name == "read_file"
    assert result.reason == "alias"


def test_repair_tool_name_resolves_unambiguous_fuzzy_match() -> None:
    result = repair_tool_name("marker_tol", _registry(_MarkerTool()))

    assert result.resolved_name == "marker_tool"
    assert result.reason == "fuzzy"


def test_repair_tool_name_does_not_guess_ambiguous_fuzzy_match() -> None:
    class MarkerTaskTool(_MarkerTool):
        name = "marker_task"

    result = repair_tool_name("marker_too", _registry(_MarkerTool(), MarkerTaskTool()))

    assert result.resolved_name is None
    assert result.reason == "unknown"
    assert result.suggestions == ("marker_tool", "marker_task")


def test_invalid_tool_result_is_structured_and_actionable() -> None:
    result = build_invalid_tool_result(
        tool_use_id="toolu_bad",
        requested_name="marker_too",
        available_names=("marker_tool", "marker_task"),
        suggestions=("marker_tool", "marker_task"),
    )

    payload = json.loads(result.content)

    assert result.is_error is True
    assert payload["error_type"] == "invalid_tool"
    assert payload["requested_tool"] == "marker_too"
    assert payload["suggestions"] == ["marker_tool", "marker_task"]
    assert payload["available_tools"] == ["marker_task", "marker_tool"]
    assert "Use one of the available tool names exactly" in payload["message"]
