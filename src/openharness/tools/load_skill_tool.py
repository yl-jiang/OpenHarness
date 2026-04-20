"""Tool for loading skill contents into context."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.skills import load_skill_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class LoadSkillToolInput(BaseModel):
    """Arguments for skill lookup."""

    name: str | None = Field(
        default=None,
        description=(
            "Name of the skill to load (case-insensitive). "
            "Leave null or omit to list all available skills first — always do this "
            "if you are unsure which skills exist before attempting to load one."
        ),
    )


class LoadSkillTool(BaseTool):
    """Load a skill's instruction content into context, or list available skills.

    Skills are reusable Markdown instruction templates that define specialised
    workflows (e.g. 'test', 'review', 'commit', 'debug').  Once loaded, the
    agent should read and follow the skill's instructions precisely.

    Typical usage pattern:
      1. Call load_skill(name=null) to see what skills are available.
      2. Call load_skill(name="<skill-name>") to load the chosen skill.
      3. Execute the workflow described in the skill content.
    """

    name = "load_skill"
    description = (
        "Load a reusable instruction template (skill) into context, or list all available skills. "
        "Skills are Markdown files that define specialised workflows such as 'test', 'review', "
        "'commit', or 'debug'. "
        "Pass name=null (or omit it) to discover available skill names first. "
        "Once a skill is loaded, read its content carefully and follow its instructions. "
        "Do NOT invent skill names — always list first if unsure."
    )
    input_model = LoadSkillToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": (
                            "Name of the skill to load (case-insensitive). "
                            "Omit to list all available skills."
                        ),
                    },
                },
            },
        }

    def is_read_only(self, arguments: LoadSkillToolInput) -> bool:
        del arguments
        return True

    async def execute(self, arguments: LoadSkillToolInput, context: ToolExecutionContext) -> ToolResult:
        registry = load_skill_registry(
            context.cwd,
            extra_skill_dirs=context.metadata.get("extra_skill_dirs"),
            extra_plugin_roots=context.metadata.get("extra_plugin_roots"),
        )

        # No name supplied → list all available skills.
        if not arguments.name:
            skills = registry.list_skills()
            if not skills:
                return ToolResult(output="No skills available.")
            lines = [f"Available skills ({len(skills)}):", ""]
            for skill in skills:
                lines.append(f"  {skill.name}  [{skill.source}]  — {skill.description}")
            lines.append("")
            lines.append("Call load_skill(name='<skill-name>') to load a skill's instructions.")
            return ToolResult(output="\n".join(lines))

        # Try case-insensitive lookup variants.
        skill = (
            registry.get(arguments.name)
            or registry.get(arguments.name.lower())
            or registry.get(arguments.name.title())
        )
        if skill is None:
            available = [s.name for s in registry.list_skills()]
            hint = f"Available skills: {', '.join(available)}" if available else "No skills are currently installed."
            return ToolResult(
                output=f"Skill not found: '{arguments.name}'. {hint}",
                is_error=True,
            )

        return ToolResult(output=skill.content)
