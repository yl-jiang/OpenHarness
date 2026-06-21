"""Return the current goal snapshot (objective, status, budgets, usage)."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

GET_GOAL_TOOL_NAME = "get_goal"


class GetGoalToolInput(BaseModel):
    """No arguments."""


_GET_GOAL_DESCRIPTION = (
    "Return the current goal snapshot: objective, completion criterion, "
    "status, progress, and budgets. Returns null when no goal is set."
)


class GetGoalTool(BaseTool):
    """Read the current goal."""

    name = GET_GOAL_TOOL_NAME
    description = _GET_GOAL_DESCRIPTION
    input_model = GetGoalToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {"type": "object", "properties": {}},
        }

    def is_read_only(self, arguments: GetGoalToolInput) -> bool:
        return True

    async def execute(
        self,
        arguments: GetGoalToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        goal_mode = context.metadata.get(GOAL_MODE_KEY)
        snapshot = goal_mode.get_goal() if isinstance(goal_mode, GoalMode) else None
        payload = {"goal": snapshot.to_dict() if snapshot is not None else None}
        return ToolResult(output=json.dumps(payload, ensure_ascii=False, indent=2))
