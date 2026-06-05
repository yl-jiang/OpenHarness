"""List all available skills."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openharness.skills import load_skill_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillListInput(BaseModel):
    """No arguments required."""


class SkillListTool(BaseTool):
    """List all available skills (bundled + user + project + plugin)."""

    name = "skill_list"
    description = (
        "List all available skills (bundled, user-defined, project-local, and "
        "plugin-contributed). Returns each skill's name, source, and one-line "
        "description. Use this to get an overview before choosing a skill to "
        "load with `skill_load` or to discover skills with `skill_search`."
    )
    input_model = SkillListInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        }

    def is_read_only(self, arguments: SkillListInput) -> bool:
        return True

    async def execute(
        self, arguments: SkillListInput, context: ToolExecutionContext
    ) -> ToolResult:
        registry = load_skill_registry(
            context.metadata.get("skill_registry_cwd", context.cwd),
            extra_skill_dirs=context.metadata.get("extra_skill_dirs"),
            extra_plugin_roots=context.metadata.get("extra_plugin_roots"),
        )
        skills = registry.list_skills()
        if not skills:
            return ToolResult(output="No skills available.")
        lines = [f"Available skills ({len(skills)}):", ""]
        for skill in skills:
            lines.append(f"  {skill.name}  [{skill.source}]  — {skill.description}")
        lines.append("")
        lines.append(
            "Use `skill_load(name='<skill_name>')` to load a skill's instructions, "
            "or `skill_search(query='...')` to find relevant skills."
        )
        return ToolResult(output="\n".join(lines))
