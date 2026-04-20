"""Tool for deleting teams."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TeamDeleteToolInput(BaseModel):
    """Arguments for deleting a team."""

    name: str = Field(description="Team name")


class TeamDeleteTool(BaseTool):
    """Delete an in-memory team."""

    name = "team_delete"
    description = "Delete an in-memory team."
    input_model = TeamDeleteToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Team name",
                    },
                },
                "required": ["name"],
            },
        }

    async def execute(self, arguments: TeamDeleteToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            get_team_registry().delete_team(arguments.name)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"Deleted team {arguments.name}")
