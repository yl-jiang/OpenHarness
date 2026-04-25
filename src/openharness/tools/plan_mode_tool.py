"""Tool for toggling plan permission mode."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.config.settings import load_settings, save_settings
from openharness.permissions import PermissionMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class PlanModeToolInput(BaseModel):
    action: Literal["enter", "exit"] = Field(
        description='Use "enter" to switch to plan mode, "exit" to return to default mode.'
    )


class PlanModeTool(BaseTool):
    """Enter or exit plan permission mode."""

    name = "plan_mode"
    description = (
        'Toggle plan permission mode. Use action="enter" to switch to plan mode '
        '(read-only, no file edits), or action="exit" to return to default mode.'
    )
    input_model = PlanModeToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["enter", "exit"],
                        "description": (
                            '"enter" switches to plan mode; '
                            '"exit" returns to default mode.'
                        ),
                    }
                },
                "required": ["action"],
            },
        }

    async def execute(self, arguments: PlanModeToolInput, context: ToolExecutionContext) -> ToolResult:
        del context
        settings = load_settings()
        if arguments.action == "enter":
            settings.permission.mode = PermissionMode.PLAN
            save_settings(settings)
            return ToolResult(output="Permission mode set to plan")
        else:
            settings.permission.mode = PermissionMode.DEFAULT
            save_settings(settings)
            return ToolResult(output="Permission mode set to default")
