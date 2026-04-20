"""Tool for leaving plan permission mode."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openharness.config.settings import load_settings, save_settings
from openharness.permissions import PermissionMode
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class ExitPlanModeToolInput(BaseModel):
    """No-op input model."""


class ExitPlanModeTool(BaseTool):
    """Switch settings permission mode back to default."""

    name = "exit_plan_mode"
    description = "Switch permission mode back to default."
    input_model = ExitPlanModeToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
            },
        }

    async def execute(self, arguments: ExitPlanModeToolInput, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        settings = load_settings()
        settings.permission.mode = PermissionMode.DEFAULT
        save_settings(settings)
        return ToolResult(output="Permission mode set to default")
