"""Tool for retrieving task details."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskGetToolInput(BaseModel):
    """Arguments for task lookup."""

    task_id: str = Field(description="Task identifier")


class TaskGetTool(BaseTool):
    """Return detailed task state."""

    name = "task_get"
    description = "Get details for a background task."
    input_model = TaskGetToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Task identifier",
                    },
                },
                "required": ["task_id"],
            },
        }

    def is_read_only(self, arguments: TaskGetToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskGetToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        task = get_task_manager().get_task(arguments.task_id)
        if task is None:
            return ToolResult(output=f"No task found with ID: {arguments.task_id}", is_error=True)
        return ToolResult(output=str(task))
