"""Enqueue a follow-up goal to run after the current one finishes."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

QUEUE_GOAL_TOOL_NAME = "queue_goal"


class QueueGoalToolInput(BaseModel):
    objective: str = Field(description="Objective of the follow-up goal.")
    completion_criterion: str | None = Field(
        default=None,
        description="Optional validation condition for the follow-up goal.",
    )
    priority: int = Field(
        default=0,
        description=(
            "Higher values start sooner. Default 0. Most follow-ups should "
            "stay at 0; use higher only when the user explicitly wants this "
            "one to run before other queued items."
        ),
    )


_QUEUE_GOAL_DESCRIPTION = (
    "Enqueue a follow-up goal to start after the current goal finishes. "
    "Use when you identify a natural next step but the current goal is "
    "not yet done. Does NOT interrupt the current goal. Do NOT use this "
    "to replace the active goal — use UpdateGoal or CreateGoal(replace=True) "
    "for that."
)


class QueueGoalTool(BaseTool):
    """Enqueue a follow-up goal to run after the current one finishes."""

    name = QUEUE_GOAL_TOOL_NAME
    description = _QUEUE_GOAL_DESCRIPTION
    input_model = QueueGoalToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {
                        "type": "string",
                        "description": "Objective of the follow-up goal.",
                    },
                    "completion_criterion": {
                        "type": "string",
                        "description": (
                            "Optional validation condition for the follow-up goal."
                        ),
                    },
                    "priority": {
                        "type": "integer",
                        "description": (
                            "Higher values start sooner. Default 0."
                        ),
                        "default": 0,
                    },
                },
                "required": ["objective"],
            },
        }

    async def execute(
        self,
        arguments: QueueGoalToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        queue = context.metadata.get(GOAL_QUEUE_KEY)
        if not isinstance(queue, GoalQueueStore):
            # Build one on the fly so the tool still works even if the
            # runtime forgot to inject the queue store.
            queue = GoalQueueStore(context.metadata)
            context.metadata[GOAL_QUEUE_KEY] = queue
        try:
            queued = queue.enqueue(
                arguments.objective,
                priority=arguments.priority,
                completion_criterion=arguments.completion_criterion,
            )
        except ValueError as exc:
            return ToolResult(is_error=True, output=str(exc))
        return ToolResult(
            output=json.dumps(
                {
                    "queued": queued.to_dict(),
                    "queue_length": len(queue),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
