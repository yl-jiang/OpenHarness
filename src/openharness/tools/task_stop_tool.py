"""Tool for stopping tasks."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskStopToolInput(BaseModel):
    """Arguments for stopping a task."""

    task_id: str = Field(description="Task identifier")


class TaskStopTool(BaseTool):
    """Stop a background task."""

    name = "task_stop"
    description = "Stop a background task."
    input_model = TaskStopToolInput

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

    async def execute(self, arguments: TaskStopToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            task = await get_task_manager().stop_task(arguments.task_id)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"Stopped task {task.id}")
