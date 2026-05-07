from __future__ import annotations

from pathlib import Path

import pytest

from openharness.engine.messages import ToolResultBlock
from openharness.engine.tool_pipeline import (
    ToolExecutionPipeline,
    ToolPipelineStage,
    ToolPipelineState,
)


def test_default_tool_pipeline_stage_order_is_explicit() -> None:
    from openharness.engine.query import _default_tool_pipeline_stages

    assert tuple(s.name for s in _default_tool_pipeline_stages()) == (
        "resolve_tool",
        "pre_hook",
        "validate_input",
        "check_permission",
        "execute_tool",
        "normalize_result",
        "update_metadata",
        "post_hook",
    )


@pytest.mark.asyncio
async def test_tool_pipeline_runs_stages_until_short_circuit() -> None:
    seen: list[str] = []

    async def _first(state: ToolPipelineState) -> ToolPipelineState:
        seen.append("first")
        return state

    async def _stop(state: ToolPipelineState) -> ToolPipelineState:
        seen.append("stop")
        state.result = ToolResultBlock(tool_use_id=state.tool_use_id, content="blocked", is_error=True)
        state.stop = True
        return state

    async def _never(state: ToolPipelineState) -> ToolPipelineState:
        seen.append("never")
        return state

    pipeline = ToolExecutionPipeline(
        [
            ToolPipelineStage("first", _first),
            ToolPipelineStage("stop", _stop),
            ToolPipelineStage("never", _never),
        ]
    )

    result = await pipeline.run(
        context=object(),
        tool_name="demo",
        tool_use_id="toolu_demo",
        tool_input={},
    )

    assert result.content == "blocked"
    assert seen == ["first", "stop"]


@pytest.mark.asyncio
async def test_execute_tool_call_delegates_to_tool_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from openharness.config.settings import PermissionSettings
    from openharness.engine.query import QueryContext, _execute_tool_call
    from openharness.permissions import PermissionChecker
    from openharness.tools.base import ToolRegistry

    captured: dict[str, object] = {}

    class _NoopApiClient:
        pass

    class _FakePipeline:
        def __init__(self, *args: object) -> None:
            del args

        async def run(
            self,
            *,
            context: QueryContext,
            tool_name: str,
            tool_use_id: str,
            tool_input: dict[str, object],
        ) -> ToolResultBlock:
            captured.update(
                {
                    "context": context,
                    "tool_name": tool_name,
                    "tool_use_id": tool_use_id,
                    "tool_input": tool_input,
                }
            )
            return ToolResultBlock(tool_use_id=tool_use_id, content="from pipeline")

    context = QueryContext(
        api_client=_NoopApiClient(),  # type: ignore[arg-type]
        tool_registry=ToolRegistry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
    )
    monkeypatch.setattr("openharness.engine.query.ToolExecutionPipeline", _FakePipeline)

    result = await _execute_tool_call(context, "demo", "toolu_demo", {"value": "x"})

    assert result.content == "from pipeline"
    assert captured == {
        "context": context,
        "tool_name": "demo",
        "tool_use_id": "toolu_demo",
        "tool_input": {"value": "x"},
    }
