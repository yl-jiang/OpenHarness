"""Permanently remove a user-created skill."""

from __future__ import annotations

import shutil
from typing import Any

from pydantic import BaseModel, Field

from openharness.skills.skill_utils import resolve_user_skills_dir
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillDeleteInput(BaseModel):
    """Arguments for skill_delete."""

    name: str = Field(
        description="Skill name (case-insensitive). Only user-created skills can be deleted.",
    )


class SkillDeleteTool(BaseTool):
    """Permanently remove a user-created skill."""

    name = "skill_delete"
    description = (
        "Permanently delete a user-created skill and its supporting files from "
        "~/.openharness/skills/<name>/. Only user-created skills can be deleted; "
        "bundled and plugin-contributed skills are managed by their source."
    )
    input_model = SkillDeleteInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name (case-insensitive).",
                    },
                },
                "required": ["name"],
            },
        }

    def is_read_only(self, arguments: SkillDeleteInput) -> bool:
        return False

    async def execute(
        self, arguments: SkillDeleteInput, context: ToolExecutionContext
    ) -> ToolResult:
        normalised = arguments.name.lower().strip()
        skill_dir = resolve_user_skills_dir(context) / normalised

        if not skill_dir.exists() or not (skill_dir / "SKILL.md").exists():
            return ToolResult(
                output=(
                    f"User skill '{normalised}' not found. "
                    "Only user-created skills can be deleted."
                ),
                is_error=True,
            )

        shutil.rmtree(skill_dir)

        refresher = context.metadata.get("system_prompt_refresher")
        if callable(refresher):
            refresher()

        return ToolResult(output=f"Skill '{normalised}' deleted.")
