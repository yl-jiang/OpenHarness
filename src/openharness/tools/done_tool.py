"""Tool for explicitly signaling task completion in full_auto mode."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


DONE_TOOL_NAME = "done"


class DoneToolInput(BaseModel):
    """Arguments for the done tool."""

    message: str = Field(description="A brief summary of what was accomplished")


class DoneTool(BaseTool):
    """Signal that the current task is complete.

    In full_auto mode the agent loop does NOT terminate when the model stops
    calling tools — it only terminates when this tool is explicitly invoked.
    """

    name = DONE_TOOL_NAME
    description = (
        "Call this tool to signal that you have finished the current task. "
        "Provide a brief summary of what was accomplished. "
        "The agent loop will NOT end until you call this tool."
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
                        "description": "A brief summary of what was accomplished",
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
        return ToolResult(output=f"Task completed: {arguments.message}")
