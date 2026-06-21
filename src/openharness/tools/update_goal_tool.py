"""Set the status of the current goal (active/complete/paused/blocked)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.goal.state import GOAL_MODE_KEY, GoalMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

UPDATE_GOAL_TOOL_NAME = "update_goal"

# Metadata key set on ToolResult when the driver should stop the current turn.
# The turn loop (post_tool_stage in turn_stages.py) reads this and sets
# ``state.action = TurnAction.STOP``. We reuse ToolResult.metadata rather than
# adding a field to the frozen dataclass — see design §1.4 / plan Phase 1.4.
GOAL_STOP_TURN_KEY = "goal_stop_turn"


class UpdateGoalToolInput(BaseModel):
    status: Literal["active", "complete", "paused", "blocked"] = Field(
        description=(
            "New goal status. `complete` (no d) signals the objective is "
            "satisfied; `blocked` signals an external condition prevents "
            "progress; `paused` yields control without terminal judgment; "
            "`active` resumes a paused/blocked goal."
        ),
    )
    reason: str | None = Field(
        default=None,
        description="Optional short reason (required for blocked; useful for complete).",
    )


_UPDATE_GOAL_DESCRIPTION = (
    "Set the status of the current goal. This is how you resume, end, or "
    "yield an autonomous goal.\n"
    "- active: resume a paused or blocked goal\n"
    "- complete: the objective is satisfied\n"
    "- blocked: an external condition prevents progress\n"
    "- paused: set the goal aside without ending it\n"
    "For terminal statuses, call this tool alone in the turn."
)


class UpdateGoalTool(BaseTool):
    """Update the status of the current goal."""

    name = UPDATE_GOAL_TOOL_NAME
    description = _UPDATE_GOAL_DESCRIPTION
    input_model = UpdateGoalToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["active", "complete", "paused", "blocked"],
                        "description": (
                            "New goal status. `complete` (no d) signals the "
                            "objective is satisfied; `blocked` signals an "
                            "external condition prevents progress; `paused` "
                            "yields control; `active` resumes."
                        ),
                    },
                    "reason": {
                        "type": "string",
                        "description": (
                            "Optional short reason (required for blocked; "
                            "useful for complete/paused)."
                        ),
                    },
                },
                "required": ["status"],
            },
        }

    async def execute(
        self,
        arguments: UpdateGoalToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        goal_mode = context.metadata.get(GOAL_MODE_KEY)
        if not isinstance(goal_mode, GoalMode):
            return ToolResult(
                is_error=True,
                output="Goal mode is not available in this session.",
            )

        stop_meta = {GOAL_STOP_TURN_KEY: True}
        status = arguments.status

        if status == "complete":
            try:
                snapshot = goal_mode.mark_complete(reason=arguments.reason, actor="model")
            except ValueError as exc:
                return ToolResult(is_error=True, output=str(exc))
            if snapshot is None:
                return ToolResult(is_error=True, output="No current goal to complete.")
            return ToolResult(
                output="Goal marked complete. Write a short completion summary for the user.",
                metadata=stop_meta,
            )

        if status == "blocked":
            try:
                snapshot = goal_mode.mark_blocked(reason=arguments.reason, actor="model")
            except ValueError as exc:
                return ToolResult(is_error=True, output=str(exc))
            if snapshot is None:
                return ToolResult(is_error=True, output="No current goal to mark blocked.")
            return ToolResult(
                output="Goal marked blocked. Briefly explain the blocker to the user.",
                metadata=stop_meta,
            )

        if status == "paused":
            try:
                goal_mode.pause_goal(reason=arguments.reason, actor="model")
            except ValueError as exc:
                return ToolResult(is_error=True, output=str(exc))
            return ToolResult(
                output="Goal paused. Briefly tell the user what was done and why you paused.",
                metadata=stop_meta,
            )

        # status == "active": resume, no stop signal (driver keeps iterating).
        try:
            goal_mode.resume_goal(reason=arguments.reason, actor="model")
        except ValueError as exc:
            return ToolResult(is_error=True, output=str(exc))
        return ToolResult(output="Goal resumed. Continue working toward the objective.")
