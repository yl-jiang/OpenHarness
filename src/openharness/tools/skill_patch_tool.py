"""Surgical find-and-replace within an existing SKILL.md."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.skills.skill_utils import (
    resolve_user_skills_dir,
    validate_frontmatter,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillPatchInput(BaseModel):
    """Arguments for skill_patch."""

    name: str = Field(
        description=(
            "Skill name (case-insensitive). Only user-created skills can be patched."
        ),
    )
    old_str: str = Field(
        description=(
            "Exact text to find inside SKILL.md. Must match exactly once — "
            "add surrounding context if needed."
        ),
    )
    new_str: str = Field(
        description="Replacement text. Use an empty string to delete matched text.",
    )


class SkillPatchTool(BaseTool):
    """Surgical find-and-replace within a user skill's SKILL.md."""

    name = "skill_patch"
    description = (
        "Apply a targeted find-and-replace to a user-created skill's SKILL.md. "
        "Use this to fix stale instructions, add missing steps, or record new "
        "pitfalls immediately after discovering them, without rewriting the "
        "entire skill."
    )
    input_model = SkillPatchInput

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
                    "old_str": {
                        "type": "string",
                        "description": "Unique text to find in SKILL.md.",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text (empty string to delete).",
                    },
                },
                "required": ["name", "old_str", "new_str"],
            },
        }

    def is_read_only(self, arguments: SkillPatchInput) -> bool:
        return False

    async def execute(
        self, arguments: SkillPatchInput, context: ToolExecutionContext
    ) -> ToolResult:
        normalised = arguments.name.lower().strip()
        skill_path = resolve_user_skills_dir(context) / normalised / "SKILL.md"

        if not skill_path.exists():
            return ToolResult(
                output=(
                    f"User skill '{normalised}' not found. "
                    "Only user-created skills can be patched."
                ),
                is_error=True,
            )

        content = skill_path.read_text(encoding="utf-8")
        count = content.count(arguments.old_str)

        if count == 0:
            preview = content[:400] + ("..." if len(content) > 400 else "")
            return ToolResult(
                output=(
                    f"old_str not found in SKILL.md for skill '{normalised}'.\n\n"
                    f"File preview:\n{preview}"
                ),
                is_error=True,
            )
        if count > 1:
            return ToolResult(
                output=(
                    f"old_str matches {count} locations in SKILL.md for '{normalised}'. "
                    "Add more surrounding context to make it unique."
                ),
                is_error=True,
            )

        new_content = content.replace(arguments.old_str, arguments.new_str, 1)

        fm_err = validate_frontmatter(new_content)
        if fm_err:
            return ToolResult(
                output=f"Patch would break SKILL.md structure: {fm_err}",
                is_error=True,
            )

        skill_path.write_text(new_content, encoding="utf-8")

        refresher = context.metadata.get("system_prompt_refresher")
        if callable(refresher):
            refresher()

        return ToolResult(output=f"Skill '{normalised}' patched successfully.")
