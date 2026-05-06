"""Task completion control tool."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class DoneToolInput(BaseModel):
    """Arguments for explicit task completion."""

    message: str = Field(
        min_length=1,
        description="Final user-facing response for the completed task",
    )


class DoneTool(BaseTool):
    """Signal that the current task is complete."""

    name = "done"
    description = (
        "Signal that the current task is complete. Use this exactly once as the final "
        "action, with the complete final response in message."
    )
    input_model = DoneToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Final user-facing response for the completed task.",
                    },
                },
                "required": ["message"],
            },
        }

    def is_read_only(self, arguments: DoneToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: DoneToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        return ToolResult(output=arguments.message.strip())
