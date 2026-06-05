"""Create a new user skill (or overwrite an existing one)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openharness.skills.skill_utils import (
    MAX_CONTENT_CHARS,
    resolve_user_skills_dir,
    validate_frontmatter,
    validate_name,
)
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class SkillWriteInput(BaseModel):
    """Arguments for skill_write."""

    name: str = Field(
        description=(
            "Skill name. Lowercase, digits, hyphens and underscores allowed "
            "(e.g. 'code-review', 'my_workflow')."
        ),
    )
    content: str = Field(
        description=(
            "Full Markdown content for the SKILL.md file.\n\n"
            "Must open with a YAML frontmatter block:\n"
            "---\n"
            "name: my-skill\n"
            "description: One-line summary of what this skill does\n"
            "---\n\n"
            "After the frontmatter, write the skill body in plain Markdown. "
            "Good skills are concise and action-oriented: explain the goal, "
            "list the key steps, and call out pitfalls."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description="Set true to replace an already-existing skill. Default false.",
    )


class SkillWriteTool(BaseTool):
    """Create or overwrite a user skill."""

    name = "skill_write"
    description = (
        "Create a new user skill at ~/.openharness/skills/<name>/SKILL.md, "
        "or overwrite an existing one when overwrite=true. The content must "
        "include YAML frontmatter with 'name' and 'description' fields "
        "followed by a Markdown workflow body."
    )
    input_model = SkillWriteInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name."},
                    "content": {
                        "type": "string",
                        "description": (
                            "Full SKILL.md content (YAML frontmatter + Markdown body)."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true to replace an existing skill.",
                        "default": False,
                    },
                },
                "required": ["name", "content"],
            },
        }

    def is_read_only(self, arguments: SkillWriteInput) -> bool:
        return False

    async def execute(
        self, arguments: SkillWriteInput, context: ToolExecutionContext
    ) -> ToolResult:
        normalised = arguments.name.lower().strip()
        name_err = validate_name(normalised)
        if name_err:
            return ToolResult(output=name_err, is_error=True)

        content_err = validate_frontmatter(arguments.content)
        if content_err:
            return ToolResult(
                output=f"Skill content does not meet format requirements: {content_err}",
                is_error=True,
            )

        if len(arguments.content) > MAX_CONTENT_CHARS:
            return ToolResult(
                output=(
                    f"Content is {len(arguments.content):,} characters "
                    f"(limit: {MAX_CONTENT_CHARS:,}). Consider splitting into "
                    "smaller files."
                ),
                is_error=True,
            )

        skill_dir = resolve_user_skills_dir(context) / normalised
        skill_path = skill_dir / "SKILL.md"
        existed = skill_path.exists()

        if existed and not arguments.overwrite:
            return ToolResult(
                output=(
                    f"Skill '{normalised}' already exists at {skill_path}. "
                    "Set overwrite=true to replace it."
                ),
                is_error=True,
            )

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(arguments.content, encoding="utf-8")

        refresher = context.metadata.get("system_prompt_refresher")
        if callable(refresher):
            refresher()

        action = "updated" if existed else "created"
        return ToolResult(
            output=(
                f"Skill '{normalised}' {action} at {skill_path}. "
                f"Use `skill_load(name='{normalised}')` to load it."
            ),
        )
