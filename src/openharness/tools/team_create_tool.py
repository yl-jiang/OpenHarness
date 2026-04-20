"""Tool for creating teams."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.coordinator.coordinator_mode import get_team_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class TeamCreateToolInput(BaseModel):
    """Arguments for creating a team."""

    name: str = Field(description="Team name")
    description: str = Field(default="", description="Team description")


class TeamCreateTool(BaseTool):
    """Create an in-memory team."""

    name = "team_create"
    description = "Create a lightweight in-memory team for agent tasks."
    input_model = TeamCreateToolInput

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
                    "description": {
                        "type": "string",
                        "description": "Team description",
                        "default": "",
                    },
                },
                "required": ["name"],
            },
        }

    async def execute(self, arguments: TeamCreateToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        try:
            team = get_team_registry().create_team(arguments.name, arguments.description)
        except ValueError as exc:
            return ToolResult(output=str(exc), is_error=True)
        return ToolResult(output=f"Created team {team.name}")
