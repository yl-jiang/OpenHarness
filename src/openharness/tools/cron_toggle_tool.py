"""Tool for enabling or disabling local cron jobs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.services.cron import set_job_enabled
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class CronToggleToolInput(BaseModel):
    """Arguments for toggling a cron job."""

    name: str = Field(description="Cron job name")
    enabled: bool = Field(description="True to enable, False to disable")


class CronToggleTool(BaseTool):
    """Enable or disable a local cron job."""

    name = "cron_toggle"
    description = "Enable or disable a local cron job by name."
    input_model = CronToggleToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Cron job name",
                    },
                    "enabled": {
                        "type": "boolean",
                        "description": "True to enable, False to disable",
                    },
                },
                "required": ["name", "enabled"],
            },
        }

    async def execute(
        self,
        arguments: CronToggleToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        del context
        if not set_job_enabled(arguments.name, arguments.enabled):
            return ToolResult(
                output=f"Cron job not found: {arguments.name}",
                is_error=True,
            )
        state = "enabled" if arguments.enabled else "disabled"
        return ToolResult(output=f"Cron job '{arguments.name}' is now {state}")
