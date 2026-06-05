"""Shared helpers used by the individual skill_* tools.

This module centralises validation, output formatting, and the user-skills
directory resolution so each ``skill_*`` tool file stays focused on a
single action.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field as dataclass_field
from pathlib import Path
from typing import Any

import yaml

from openharness.skills import get_user_skills_dir
from openharness.tools.base import ToolExecutionContext

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_CONTENT_CHARS = 100_000
SKILL_FILE_SAMPLE_LIMIT = 10


@dataclass
class SkillValidationResult:
    """Outcome of :func:`validate_skill_content`."""

    errors: list[str] = dataclass_field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_skill_content(content: str) -> SkillValidationResult:
    """Validate that *content* conforms to the SKILL.md format requirements."""
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


def validate_name(name: str) -> str | None:
    """Return an error message if *name* is invalid, else None."""
    if not NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. "
            "Use lowercase letters, digits, hyphens, or underscores only "
            "(e.g. 'code-review', 'my_workflow')."
        )
    return None


def validate_frontmatter(content: str) -> str | None:
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


def sample_skill_files(skill_dir: Path, *, limit: int = SKILL_FILE_SAMPLE_LIMIT) -> list[str]:
    if limit <= 0:
        return []

    def _python_fallback() -> list[str]:
        fallback_files: list[str] = []
        for path in sorted(skill_dir.rglob("*")):
            if len(fallback_files) >= limit or not path.is_file():
                continue
            if path.name.lower() == "skill.md":
                continue
            fallback_files.append(str(path.resolve()))
        return fallback_files

    rg = shutil.which("rg")
    if not rg:
        return _python_fallback()

    files: list[str] = []
    try:
        process = subprocess.Popen(
            [rg, "--files", "--hidden"],
            cwd=skill_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except FileNotFoundError:
        return _python_fallback()
    stdout = process.stdout
    if stdout is None:
        process.wait(timeout=1)
        return files

    try:
        while len(files) < limit:
            line = stdout.readline()
            if not line:
                break
            relative = line.strip()
            if not relative or Path(relative).name.lower() == "skill.md":
                continue
            files.append(str((skill_dir / relative).resolve()))
    finally:
        close = getattr(stdout, "close", None)
        if callable(close):
            close()
        if len(files) >= limit:
            process.terminate()
        process.wait(timeout=1)
    return files


def format_loaded_skill_output(
    name: str, content: str, path: str | None
) -> tuple[str, dict[str, Any]]:
    if not path:
        return content, {}

    skill_path = Path(path)
    if skill_path.name.lower() != "skill.md":
        return content, {}

    skill_dir = skill_path.parent.resolve()
    files = sample_skill_files(skill_dir)
    parts = [
        f'<skill_content name="{name}">',
        f"# Skill: {name}",
        "",
        content.rstrip(),
        "",
        f"Base directory for this skill: {skill_dir.as_uri()}",
        "Relative paths in this skill (e.g., scripts/, references/) are relative to this base directory.",
        "Note: file list is sampled.",
        "",
        "<skill_files>",
    ]
    parts.extend(f"<file>{file_path}</file>" for file_path in files)
    parts.extend(["</skill_files>", "</skill_content>"])
    return "\n".join(parts), {"skill_name": name, "skill_dir": str(skill_dir)}


def resolve_user_skills_dir(context: ToolExecutionContext) -> Path:
    override = context.metadata.get("user_skills_dir")
    if override:
        return Path(override).expanduser().resolve()
    return get_user_skills_dir()


__all__ = [
    "MAX_CONTENT_CHARS",
    "NAME_RE",
    "SKILL_FILE_SAMPLE_LIMIT",
    "SkillValidationResult",
    "format_loaded_skill_output",
    "resolve_user_skills_dir",
    "sample_skill_files",
    "validate_frontmatter",
    "validate_name",
    "validate_skill_content",
]
