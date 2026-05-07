"""Small staged pipeline primitive for tool execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from openharness.engine.messages import ToolResultBlock

StageHandler = Callable[["ToolPipelineState"], Awaitable["ToolPipelineState"]]


@dataclass(frozen=True)
class ToolPipelineStage:
    """One named step in tool execution."""

    name: str
    handler: StageHandler


@dataclass
class ToolPipelineState:
    """Mutable state passed between tool execution stages."""

    context: Any
    tool_name: str
    tool_use_id: str
    tool_input: dict[str, object]
    result: ToolResultBlock | None = None
    stop: bool = False
    tool: Any = None
    repair: Any = None
    parsed_input: Any = None
    permission_file_path: str | None = None
    permission_command: str | None = None
    raw_result: Any = None
    artifact_path: Any = None
    extras: dict[str, object] = field(default_factory=dict)


class ToolExecutionPipeline:
    """Run tool execution as explicit, testable stages."""

    def __init__(self, stages: Sequence[ToolPipelineStage] | None = None) -> None:
        self._stages = tuple(stages or ())

    @property
    def stage_names(self) -> tuple[str, ...]:
        return tuple(stage.name for stage in self._stages)

    async def run(
        self,
        *,
        context: Any,
        tool_name: str,
        tool_use_id: str,
        tool_input: dict[str, object],
    ) -> ToolResultBlock:
        state = ToolPipelineState(
            context=context,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            tool_input=tool_input,
        )
        for stage in self._stages:
            state = await stage.handler(state)
            if state.stop:
                break
        if state.result is None:
            raise RuntimeError("tool pipeline completed without a tool result")
        return state.result
