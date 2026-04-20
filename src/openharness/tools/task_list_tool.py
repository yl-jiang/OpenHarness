"""Tool for listing tasks."""

from __future__ import annotations

from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tasks.types import TaskStatus
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_STATUS_NAMES = ", ".join(f"'{s.value}'" for s in TaskStatus)


class TaskListToolInput(BaseModel):
    """Arguments for task listing."""

    status: str | None = Field(
        default=None,
        description=(
            f"Optional status filter. Valid values: {_STATUS_NAMES}. "
            "Leave null/empty to list ALL tasks regardless of status. "
            "Do NOT pass 'all' — use null to get everything."
        ),
    )


class TaskListTool(BaseTool):
    """List background tasks."""

    name = "task_list"
    description = (
        "List background tasks. Returns all tasks when no status filter is given. "
        "Always check with no filter first to get a complete picture before filtering."
    )
    input_model = TaskListToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "running", "completed", "failed", "killed"],
                        "description": (
                            "Optional status filter. "
                            "Omit to list ALL tasks regardless of status."
                        ),
                    },
                },
            },
        }

    def is_read_only(self, arguments: TaskListToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskListToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        # Resolve the requested status to a TaskStatus enum member, falling back
        # to None (= no filter) for unrecognised values like 'all'.
        effective_status: TaskStatus | None = None
        if arguments.status is not None:
            try:
                effective_status = TaskStatus(arguments.status)
            except ValueError:
                pass  # unknown value → return all tasks so caller is never misled

        tasks = get_task_manager().list_tasks(status=effective_status)

        # Always append a global summary so the LLM cannot mistake an empty
        # filtered result for "there are no tasks at all".
        all_tasks = get_task_manager().list_tasks()
        counts = Counter(t.status for t in all_tasks)
        summary_parts = [f"{s.value}={counts[s]}" for s in TaskStatus if counts[s]]
        summary = "total={} ({})".format(len(all_tasks), ", ".join(summary_parts)) if summary_parts else "total=0"

        if not tasks:
            filter_note = f" with status='{arguments.status}'" if effective_status else ""
            return ToolResult(output=f"(no tasks{filter_note}) | {summary}")

        lines = [f"{task.id} {task.type} {task.status} {task.description}" for task in tasks]
        lines.append(f"--- {summary} ---")
        return ToolResult(output="\n".join(lines))
