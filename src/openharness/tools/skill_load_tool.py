"""Load a skill's Markdown instructions into the conversation context."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.skills.loader import load_skill_registry_cached
from openharness.skills.skill_utils import format_loaded_skill_output
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillLoadInput(BaseModel):
    """Arguments for skill_load."""

    name: str = Field(
        description=(
            "Skill name (case-insensitive). Use `skill_list` to discover "
            "available skills or `skill_search` to find one by natural language."
        ),
    )


class SkillLoadTool(BaseTool):
    """Inject a skill's Markdown instructions into the conversation."""

    name = "skill_load"
    description = (
        "Load a skill's Markdown instructions into the conversation context. "
        "Call this after choosing a skill (via `skill_list` or `skill_search`) "
        "before following its workflow."
    )
    input_model = SkillLoadInput

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

    def is_read_only(self, arguments: SkillLoadInput) -> bool:
        return True

    async def execute(
        self, arguments: SkillLoadInput, context: ToolExecutionContext
    ) -> ToolResult:
        registry = load_skill_registry_cached(
            context.metadata.get("skill_registry_cwd", context.cwd),
            extra_skill_dirs=context.metadata.get("extra_skill_dirs"),
            extra_plugin_roots=context.metadata.get("extra_plugin_roots"),
        )
        skill = (
            registry.get(arguments.name)
            or registry.get(arguments.name.lower())
            or registry.get(arguments.name.title())
        )
        if skill is None:
            available = [s.name for s in registry.list_skills()]
            hint = (
                f"Available skills: {', '.join(available)}"
                if available
                else "No skills are currently installed."
            )
            return ToolResult(
                output=f"Skill not found: '{arguments.name}'. {hint}",
                is_error=True,
            )
        output, metadata = format_loaded_skill_output(
            skill.name, skill.content, skill.path
        )
        return ToolResult(output=output, metadata=metadata)
