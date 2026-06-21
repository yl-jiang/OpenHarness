"""Create a durable, structured goal that the runtime drives across turns."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

CREATE_GOAL_TOOL_NAME = "create_goal"


class CreateGoalToolInput(BaseModel):
    objective: str = Field(description="The goal objective (what to achieve).")
    completion_criterion: str | None = Field(
        default=None,
        description=(
            "Optional validation condition that must be true before the goal "
            "can be marked complete (e.g. 'All tests pass')."
        ),
    )
    replace: bool = Field(
        default=False,
        description="If true, replace any existing goal. Otherwise fail if one exists.",
    )


_CREATE_GOAL_DESCRIPTION = (
    "Create a durable, structured goal that the runtime will pursue across "
    "multiple turns. Call only when the user explicitly asks to start a goal "
    "or work autonomously toward an outcome. Do NOT create a goal for "
    "greetings, ordinary questions, or vague requests."
)


class CreateGoalTool(BaseTool):
    """Create a new goal (or replace an existing one)."""

    name = CREATE_GOAL_TOOL_NAME
    description = _CREATE_GOAL_DESCRIPTION
    input_model = CreateGoalToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "objective": {
                        "type": "string",
                        "description": "The goal objective (what to achieve).",
                    },
                    "completion_criterion": {
                        "type": "string",
                        "description": (
                            "Optional validation condition that must be true "
                            "before the goal can be marked complete."
                        ),
                    },
                    "replace": {
                        "type": "boolean",
                        "description": (
                            "If true, replace any existing goal. Otherwise "
                            "error if one already exists."
                        ),
                        "default": False,
                    },
                },
                "required": ["objective"],
            },
        }

    async def execute(
        self,
        arguments: CreateGoalToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        goal_mode = context.metadata.get(GOAL_MODE_KEY)
        if not isinstance(goal_mode, GoalMode):
            return ToolResult(
                is_error=True,
                output="Goal mode is not available in this session.",
            )
        try:
            snapshot = goal_mode.create_goal(
                arguments.objective,
                completion_criterion=arguments.completion_criterion,
                replace=arguments.replace,
            )
        except ValueError as exc:
            return ToolResult(is_error=True, output=str(exc))

        return ToolResult(
            output=json.dumps(
                {"goal": snapshot.to_dict()},
                ensure_ascii=False,
                indent=2,
            )
        )
