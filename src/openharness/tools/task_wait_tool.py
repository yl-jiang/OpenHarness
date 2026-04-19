"""Tool for waiting until background tasks complete."""

from __future__ import annotations

import asyncio
from collections import Counter

from pydantic import BaseModel, Field

from openharness.tasks.manager import get_task_manager
from openharness.tasks.types import TaskStatus
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TaskWaitToolInput(BaseModel):
    """Arguments for task_wait."""

    task_ids: list[str] | None = Field(
        default=None,
        description=(
            "List of task IDs to wait for. "
            "Leave null/empty to wait for ALL currently running tasks."
        ),
    )
    timeout: float = Field(
        default=300.0,
        description="Maximum seconds to wait before returning with a timeout notice.",
        ge=1.0,
        le=3600.0,
    )


class TaskWaitTool(BaseTool):
    """Block until specified background tasks (or all running tasks) finish."""

    name = "task_wait"
    description = (
        "Wait (block) until all specified background tasks complete, fail, or are killed. "
        "Use this instead of a manual sleep+task_list polling loop whenever you need to "
        "synchronise with sub-agent or shell tasks before continuing. "
        "Pass task_ids to wait for specific tasks, or omit to wait for every running task."
    )
    input_model = TaskWaitToolInput

    def is_read_only(self, arguments: TaskWaitToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: TaskWaitToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        manager = get_task_manager()

        # Validate requested task IDs early so the caller gets a clear error.
        if arguments.task_ids is not None:
            unknown = [tid for tid in arguments.task_ids if manager.get_task(tid) is None]
            if unknown:
                return ToolResult(
                    output=f"Unknown task id(s): {', '.join(unknown)}",
                    is_error=True,
                )

        try:
            finished = await manager.wait_for_tasks(
                arguments.task_ids,
                timeout=arguments.timeout,
            )
        except asyncio.TimeoutError:
            # Return a non-error timeout notice so the LLM can decide what to do next.
            running = manager.list_tasks(status=TaskStatus.RUNNING)
            still_running = ", ".join(t.id for t in running)
            return ToolResult(
                output=(
                    f"Timeout after {arguments.timeout:.0f}s. "
                    f"Still running: {still_running or 'none'}"
                ),
            )

        if not finished:
            return ToolResult(output="No tasks were waiting (nothing to wait for).")

        counts: Counter[TaskStatus] = Counter(t.status for t in finished)
        summary_parts = [f"{s.value}={counts[s]}" for s in TaskStatus if counts[s]]
        summary = "total={} ({})".format(len(finished), ", ".join(summary_parts))

        lines = [f"{t.id} {t.type} {t.status} {t.description}" for t in finished]
        lines.append(f"--- {summary} ---")
        return ToolResult(output="\n".join(lines))
