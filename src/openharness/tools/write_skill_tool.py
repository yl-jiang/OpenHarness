"""Tool for creating or updating user-defined skills."""

from __future__ import annotations

import re
from typing import Any
from dataclasses import dataclass, field

import yaml

from pydantic import BaseModel, Field

from openharness.skills import get_user_skills_dir
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

# Only allow safe directory names.
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass
class SkillValidationResult:
    """Outcome of :func:`validate_skill_content`."""

    errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_skill_content(content: str) -> SkillValidationResult:
    """Validate that *content* conforms to the SKILL.md format requirements.

    Rules (all blocking errors):
    1. A ``---`` frontmatter block must be present.
    2. The frontmatter must be valid YAML.
    3. The frontmatter must contain a non-empty ``name`` string field.
    4. The frontmatter must contain a non-empty ``description`` string field.
    5. The body after the frontmatter block must not be empty (whitespace-only
       counts as empty).
    """
    result = SkillValidationResult()

    # ── Frontmatter presence ───────────────────────────────────────────────
    if not content.startswith("---\n"):
        result.errors.append(
            "Missing YAML frontmatter. The file must start with a '---' block "
            "containing at least 'name' and 'description' fields."
        )
        return result  # remaining checks are pointless without frontmatter

    end_marker = content.find("\n---\n", 4)
    if end_marker == -1:
        result.errors.append(
            "Frontmatter block is not closed. Add a closing '---' line after "
            "the YAML fields."
        )
        return result

    raw_yaml = content[4:end_marker]
    body = content[end_marker + 5:]  # everything after the closing "---\n"

    # ── YAML validity ──────────────────────────────────────────────────────
    try:
        meta = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        result.errors.append(f"Frontmatter YAML is invalid: {exc}")
        return result

    if not isinstance(meta, dict):
        result.errors.append("Frontmatter must be a YAML mapping (key: value pairs).")
        return result

    # ── Required fields ────────────────────────────────────────────────────
    name_val = meta.get("name")
    if not isinstance(name_val, str) or not name_val.strip():
        result.errors.append(
            "Frontmatter is missing a non-empty 'name' field. "
            "Example: 'name: code-review'"
        )

    desc_val = meta.get("description")
    if not isinstance(desc_val, str) or not desc_val.strip():
        result.errors.append(
            "Frontmatter is missing a non-empty 'description' field. "
            "Example: 'description: Guides the agent through a structured code review'"
        )

    # ── Body non-empty ─────────────────────────────────────────────────────
    if not body.strip():
        result.errors.append(
            "Skill body (content after the frontmatter block) is empty. "
            "Add at least a title and workflow description."
        )

    return result


_SKILL_TEMPLATE = """\
---
name: {name}
description: <one-line description of what this skill does>
---

# {title}

<content>
"""


class WriteSkillToolInput(BaseModel):
    """Arguments for creating or updating a skill."""

    name: str = Field(
        description=(
            "Skill name used as the directory name under ~/.openharness/skills/. "
            "Must be lowercase alphanumeric, hyphens, or underscores (e.g. 'code-review'). "
            "This becomes the name used with load_skill."
        ),
    )
    content: str = Field(
        description=(
            "Full Markdown content for the SKILL.md file.\n\n"
            "Required format — the file must start with a YAML frontmatter block:\n"
            "---\n"
            "name: my-skill\n"
            "description: One-line summary of what this skill does\n"
            "---\n\n"
            "After the frontmatter, write the skill body in plain Markdown. "
            "Good skills tend to be concise and action-oriented: explain the goal, "
            "describe the key steps the agent should follow, and call out any important "
            "constraints or gotchas. Avoid over-specifying — leave room for the agent "
            "to adapt to context."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description="Set true to replace an already-existing skill with the same name.",
    )


class WriteSkillTool(BaseTool):
    """Create or update a user-defined skill stored as a SKILL.md file."""

    name = "write_skill"
    description = (
        "Create a new reusable instruction template (skill) in the user skills directory "
        "(~/.openharness/skills/<name>/SKILL.md). "
        "Skills are Markdown files that define specialised workflows. "
        "After creation the skill is immediately available via load_skill. "
        "The content must be valid Markdown; starting with YAML frontmatter "
        "(name + description fields) is strongly recommended for discoverability. "
        "Set overwrite=true to update an existing skill."
    )
    input_model = WriteSkillToolInput

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
                            "Skill name used as the directory name under ~/.openharness/skills/. "
                            "Must be lowercase alphanumeric, hyphens, or underscores (e.g. 'code-review')."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full Markdown content for the SKILL.md file. "
                            "Must start with YAML frontmatter (--- block) containing name and description fields."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "Set true to replace an already-existing skill with the same name",
                        "default": False,
                    },
                },
                "required": ["name", "content"],
            },
        }

    def is_read_only(self, arguments: WriteSkillToolInput) -> bool:
        del arguments
        return False

    async def execute(self, arguments: WriteSkillToolInput, context: ToolExecutionContext) -> ToolResult:
        del context

        # Validate skill name.
        normalised = arguments.name.lower().strip()
        if not _NAME_RE.match(normalised):
            return ToolResult(
                output=(
                    f"Invalid skill name '{arguments.name}'. "
                    "Use lowercase letters, digits, hyphens, or underscores only "
                    "(e.g. 'code-review', 'my_workflow')."
                ),
                is_error=True,
            )

        # Validate content format (frontmatter + body).
        validation = validate_skill_content(arguments.content)
        if not validation.is_valid:
            joined = "\n".join(f"  - {e}" for e in validation.errors)
            return ToolResult(
                output=f"Skill content does not meet format requirements:\n{joined}",
                is_error=True,
            )

        skill_dir = get_user_skills_dir() / normalised
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

        action = "updated" if existed else "created"
        return ToolResult(
            output=(
                f"Skill '{normalised}' {action} at {skill_path}. "
                f"Use load_skill(name='{normalised}') to load it."
            ),
        )
