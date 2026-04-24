"""Unified skill manager tool — list, load, write, patch, and delete skills."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Literal, Optional

import yaml
from pydantic import BaseModel, Field

from openharness.skills import get_user_skills_dir, load_skill_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_MAX_CONTENT_CHARS = 100_000


@dataclass
class SkillValidationResult:
    """Outcome of :func:`validate_skill_content`."""

    errors: list[str] = dataclass_field(default_factory=list)

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

    if not content.startswith("---\n"):
        result.errors.append(
            "Missing YAML frontmatter. The file must start with a '---' block "
            "containing at least 'name' and 'description' fields."
        )
        return result

    end_marker = content.find("\n---\n", 4)
    if end_marker == -1:
        result.errors.append(
            "Frontmatter block is not closed. Add a closing '---' line after "
            "the YAML fields."
        )
        return result

    raw_yaml = content[4:end_marker]
    body = content[end_marker + 5:]

    try:
        meta = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        result.errors.append(f"Frontmatter YAML is invalid: {exc}")
        return result

    if not isinstance(meta, dict):
        result.errors.append("Frontmatter must be a YAML mapping (key: value pairs).")
        return result

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

    if not body.strip():
        result.errors.append(
            "Skill body (content after the frontmatter block) is empty. "
            "Add at least a title and workflow description."
        )

    return result


def _validate_name(name: str) -> str | None:
    """Return an error message if *name* is invalid, else None."""
    if not _NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. "
            "Use lowercase letters, digits, hyphens, or underscores only "
            "(e.g. 'code-review', 'my_workflow')."
        )
    return None


def _validate_frontmatter(content: str) -> str | None:
    """Validate SKILL.md content format. Returns error string or None if valid."""
    if not content.strip():
        return "Content cannot be empty."

    if not content.startswith("---\n"):
        return (
            "Missing YAML frontmatter. The file must start with a '---' block "
            "containing at least 'name' and 'description' fields."
        )

    end_marker = content.find("\n---\n", 4)
    if end_marker == -1:
        return "Frontmatter block is not closed. Add a closing '---' line after the YAML fields."

    raw_yaml = content[4:end_marker]
    body = content[end_marker + 5:]

    try:
        meta = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return f"Frontmatter YAML is invalid: {exc}"

    if not isinstance(meta, dict):
        return "Frontmatter must be a YAML mapping (key: value pairs)."

    name_val = meta.get("name")
    if not isinstance(name_val, str) or not name_val.strip():
        return "Frontmatter is missing a non-empty 'name' field. Example: 'name: code-review'"

    desc_val = meta.get("description")
    if not isinstance(desc_val, str) or not desc_val.strip():
        return "Frontmatter is missing a non-empty 'description' field."

    if not body.strip():
        return (
            "Skill body (content after the frontmatter block) is empty. "
            "Add at least a title and workflow description."
        )

    return None


class SkillManagerToolInput(BaseModel):
    """Arguments for the skill_manager tool."""

    action: Literal["list", "load", "write", "patch", "delete"] = Field(
        description=(
            "Action to perform:\n"
            "  list   — list all available skills (bundled + user-defined)\n"
            "  load   — inject a skill's Markdown instructions into context\n"
            "  write  — create a new user skill, or update one (overwrite=true)\n"
            "  patch  — targeted find-and-replace within an existing SKILL.md\n"
            "  delete — permanently remove a user-created skill"
        ),
    )
    name: Optional[str] = Field(
        default=None,
        description=(
            "Skill name (case-insensitive for 'load'; normalised to lowercase for "
            "'write'/'patch'/'delete'). Required for every action except 'list'."
        ),
    )
    content: Optional[str] = Field(
        default=None,
        description=(
            "Full Markdown content for the SKILL.md file. Required for 'write'.\n\n"
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
        description="For 'write': set true to replace an already-existing skill. Default false.",
    )
    old_string: Optional[str] = Field(
        default=None,
        description=(
            "For 'patch': exact text to find inside SKILL.md. "
            "Must match exactly once — add surrounding context if needed."
        ),
    )
    new_string: Optional[str] = Field(
        default=None,
        description=(
            "For 'patch': replacement text. "
            "Use an empty string to delete the matched text."
        ),
    )


class SkillManagerTool(BaseTool):
    """Unified skill manager: list, load, write, patch, and delete skills."""

    name = "skill_manager"
    description = (
        "Manage reusable instruction templates (skills): list all available skills, "
        "load a skill's content into context, create or update a user skill, "
        "patch an existing skill, or delete a user-created skill.\n\n"
        "Actions:\n"
        "  list   — discover all available skills (bundled + user-defined)\n"
        "  load   — inject a skill's Markdown instructions into the conversation\n"
        "  write  — create a skill or overwrite one (overwrite=true)\n"
        "  patch  — surgical find-and-replace within an existing SKILL.md\n"
        "  delete — permanently remove a user-created skill\n\n"
        "Skills are Markdown files with YAML frontmatter (name + description) "
        "followed by workflow instructions. "
        "User skills live in ~/.openharness/skills/<name>/SKILL.md."
    )
    input_model = SkillManagerToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "load", "write", "patch", "delete"],
                        "description": (
                            "Action to perform: "
                            "'list' to discover skills, "
                            "'load' to inject skill content, "
                            "'write' to create/update a skill, "
                            "'patch' for targeted SKILL.md edits, "
                            "'delete' to remove a user skill."
                        ),
                    },
                    "name": {
                        "type": "string",
                        "description": (
                            "Skill name. Required for load/write/patch/delete. "
                            "Lowercase, hyphens and underscores allowed."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": (
                            "Full SKILL.md content (YAML frontmatter + Markdown body). "
                            "Required for 'write'."
                        ),
                    },
                    "overwrite": {
                        "type": "boolean",
                        "description": "For 'write': set true to replace an existing skill.",
                        "default": False,
                    },
                    "old_string": {
                        "type": "string",
                        "description": "For 'patch': unique text to find in SKILL.md.",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "For 'patch': replacement text (empty string to delete).",
                    },
                },
                "required": ["action"],
            },
        }

    def is_read_only(self, arguments: SkillManagerToolInput) -> bool:
        return arguments.action in ("list", "load")

    async def execute(self, arguments: SkillManagerToolInput, context: ToolExecutionContext) -> ToolResult:
        if arguments.action == "list":
            return self._list(context)
        if arguments.action == "load":
            return self._load(arguments, context)

        if arguments.action == "write":
            result = self._write(arguments)
        elif arguments.action == "patch":
            result = self._patch(arguments)
        elif arguments.action == "delete":
            result = self._delete(arguments)
        else:
            return ToolResult(
                output=f"Unknown action '{arguments.action}'. Valid actions: list, load, write, patch, delete.",
                is_error=True,
            )

        # After any mutating action succeeds, refresh the system prompt so
        # the updated skill list takes effect for the current session.
        if not result.is_error:
            refresher = context.metadata.get("system_prompt_refresher")
            if callable(refresher):
                refresher()

        return result

    # ── list ────────────────────────────────────────────────────────────────

    def _list(self, context: ToolExecutionContext) -> ToolResult:
        registry = load_skill_registry(
            context.cwd,
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
        lines.append("Use action='load' with name='<skill-name>' to load a skill's instructions.")
        return ToolResult(output="\n".join(lines))

    # ── load ────────────────────────────────────────────────────────────────

    def _load(self, arguments: SkillManagerToolInput, context: ToolExecutionContext) -> ToolResult:
        if not arguments.name:
            return ToolResult(
                output="name is required for action='load'. Use action='list' to see available skills.",
                is_error=True,
            )
        registry = load_skill_registry(
            context.cwd,
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
        return ToolResult(output=skill.content)

    # ── write ────────────────────────────────────────────────────────────────

    def _write(self, arguments: SkillManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='write'.", is_error=True)
        if not arguments.content:
            return ToolResult(output="content is required for action='write'.", is_error=True)

        normalised = arguments.name.lower().strip()
        name_err = _validate_name(normalised)
        if name_err:
            return ToolResult(output=name_err, is_error=True)

        content_err = _validate_frontmatter(arguments.content)
        if content_err:
            return ToolResult(
                output=f"Skill content does not meet format requirements: {content_err}",
                is_error=True,
            )

        if len(arguments.content) > _MAX_CONTENT_CHARS:
            return ToolResult(
                output=(
                    f"Content is {len(arguments.content):,} characters "
                    f"(limit: {_MAX_CONTENT_CHARS:,}). Consider splitting into smaller files."
                ),
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
                f"Use action='load' with name='{normalised}' to load it."
            ),
        )

    # ── patch ────────────────────────────────────────────────────────────────

    def _patch(self, arguments: SkillManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='patch'.", is_error=True)
        if not arguments.old_string:
            return ToolResult(
                output="old_string is required for action='patch'. Provide the exact text to find.",
                is_error=True,
            )
        if arguments.new_string is None:
            return ToolResult(
                output="new_string is required for action='patch'. Use an empty string to delete matched text.",
                is_error=True,
            )

        normalised = arguments.name.lower().strip()
        skill_path = get_user_skills_dir() / normalised / "SKILL.md"

        if not skill_path.exists():
            return ToolResult(
                output=(
                    f"User skill '{normalised}' not found. "
                    "Only user-created skills can be patched."
                ),
                is_error=True,
            )

        content = skill_path.read_text(encoding="utf-8")
        count = content.count(arguments.old_string)

        if count == 0:
            preview = content[:400] + ("..." if len(content) > 400 else "")
            return ToolResult(
                output=(
                    f"old_string not found in SKILL.md for skill '{normalised}'.\n\n"
                    f"File preview:\n{preview}"
                ),
                is_error=True,
            )
        if count > 1:
            return ToolResult(
                output=(
                    f"old_string matches {count} locations in SKILL.md for '{normalised}'. "
                    "Add more surrounding context to make it unique."
                ),
                is_error=True,
            )

        new_content = content.replace(arguments.old_string, arguments.new_string, 1)

        fm_err = _validate_frontmatter(new_content)
        if fm_err:
            return ToolResult(
                output=f"Patch would break SKILL.md structure: {fm_err}",
                is_error=True,
            )

        skill_path.write_text(new_content, encoding="utf-8")
        return ToolResult(output=f"Skill '{normalised}' patched successfully.")

    # ── delete ───────────────────────────────────────────────────────────────

    def _delete(self, arguments: SkillManagerToolInput) -> ToolResult:
        if not arguments.name:
            return ToolResult(output="name is required for action='delete'.", is_error=True)

        normalised = arguments.name.lower().strip()
        skill_dir = get_user_skills_dir() / normalised

        if not skill_dir.exists() or not (skill_dir / "SKILL.md").exists():
            return ToolResult(
                output=(
                    f"User skill '{normalised}' not found. "
                    "Only user-created skills can be deleted."
                ),
                is_error=True,
            )

        shutil.rmtree(skill_dir)
        return ToolResult(output=f"Skill '{normalised}' deleted.")
