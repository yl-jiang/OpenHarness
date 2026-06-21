"""Set a hard budget (turns/tokens/time) on the current goal."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.goal.budget import budget_limits_from_input
from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

SET_GOAL_BUDGET_TOOL_NAME = "set_goal_budget"


class SetGoalBudgetToolInput(BaseModel):
    value: float = Field(
        description="Numeric budget value. Must be positive.",
    )
    unit: Literal["turns", "tokens", "seconds", "minutes", "hours"] = Field(
        description="Budget unit.",
    )


_SET_GOAL_BUDGET_DESCRIPTION = (
    "Record a user-stated hard runtime limit for the current goal. Accepts "
    "one limit at a time (call multiple times to set multiple limits). "
    "Time budgets must be between 1 second and 24 hours."
)


class SetGoalBudgetTool(BaseTool):
    """Set a hard budget on the current goal."""

    name = SET_GOAL_BUDGET_TOOL_NAME
    description = _SET_GOAL_BUDGET_DESCRIPTION
    input_model = SetGoalBudgetToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "value": {
                        "type": "number",
                        "description": "Numeric budget value. Must be positive.",
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["turns", "tokens", "seconds", "minutes", "hours"],
                        "description": "Budget unit.",
                    },
                },
                "required": ["value", "unit"],
            },
        }

    async def execute(
        self,
        arguments: SetGoalBudgetToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        goal_mode = context.metadata.get(GOAL_MODE_KEY)
        if not isinstance(goal_mode, GoalMode):
            return ToolResult(
                is_error=True,
                output="Goal mode is not available in this session.",
            )
        if goal_mode.get_goal() is None:
            return ToolResult(
                is_error=True,
                output="No current goal to set a budget for.",
            )
        limits = budget_limits_from_input(arguments.value, arguments.unit)
        if limits is None:
            return ToolResult(
                is_error=True,
                output=(
                    f"Goal budget not set: {arguments.value} {arguments.unit} "
                    "is not a reasonable goal budget. Time budgets must be "
                    "between 1 second and 24 hours."
                ),
            )
        snapshot = goal_mode.set_budget_limits(limits)
        return ToolResult(
            output=json.dumps(
                {"goal": snapshot.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
