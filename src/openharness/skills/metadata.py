"""Skill markdown metadata parsing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from openharness.skills.types import SkillDefinition
from openharness.utils.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SkillMarkdownMetadata:
    """Parsed skill metadata extracted from markdown frontmatter."""

    name: str
    description: str
    declared_name: str | None = None
    description_source: str = "fallback"
    version: str | None = None
    tags: tuple[str, ...] = ()
    author: str | None = None
    license: str | None = None
    allowed_tools: tuple[str, ...] = ()
    required_context: tuple[str, ...] = ()
    argument_hint: str | None = None
    context: str | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    shell_injection: bool = False

def load_skill_definition(
    default_name: str,
    content: str,
    *,
    source: str,
    path: str | Path | None = None,
) -> SkillDefinition | None:
    """Build and validate a SkillDefinition from markdown content."""
    metadata = parse_skill_markdown(default_name, content, path=path)
    reason = _skill_skip_reason(metadata, default_name, path=path)
    location = str(path) if path is not None else default_name
    if reason is not None:
        logger.warning("Skipping skill load from %s [source=%s]: %s", location, source, reason)
        return None
    skill = _build_skill_definition_from_metadata(
        metadata,
        content,
        source=source,
        path=path,
    )
    logger.info("Loaded skill %s from %s [source=%s]", skill.name, location, source)
    return skill


def _build_skill_definition_from_metadata(
    metadata: SkillMarkdownMetadata,
    content: str,
    *,
    source: str,
    path: str | Path | None = None,
) -> SkillDefinition:
    """Convert parsed markdown metadata into a SkillDefinition."""
    return SkillDefinition(
        name=metadata.name,
        description=metadata.description,
        content=content,
        source=source,
        path=str(path) if path is not None else None,
        version=metadata.version,
        tags=metadata.tags,
        author=metadata.author,
        license=metadata.license,
        allowed_tools=metadata.allowed_tools,
        required_context=metadata.required_context,
        argument_hint=metadata.argument_hint,
        context=metadata.context,
        disable_model_invocation=metadata.disable_model_invocation,
        user_invocable=metadata.user_invocable,
        shell_injection=metadata.shell_injection,
    )


def parse_skill_markdown(
    default_name: str,
    content: str,
    *,
    path: str | Path | None = None,
) -> SkillMarkdownMetadata:
    """Parse skill metadata from markdown with optional YAML frontmatter."""
    name = default_name
    description = ""
    declared_name: str | None = None
    description_source = "fallback"
    frontmatter, body = _parse_skill_frontmatter(content, path=path)

    declared_name = _parse_optional_string(frontmatter.get("name"))
    if declared_name is not None:
        name = declared_name

    frontmatter_description = _parse_optional_string(frontmatter.get("description"))
    if frontmatter_description is not None:
        description = frontmatter_description
        description_source = "frontmatter"
    else:
        heading = _extract_skill_heading(body)
        if heading and name == default_name:
            name = heading

        body_description = _extract_skill_body_description(body)
        if body_description is not None:
            description = body_description
            description_source = "body"

    if not description:
        description = f"Skill: {name}"

    version = _parse_optional_string(frontmatter.get("version"))
    tags = _parse_string_tuple(frontmatter.get("tags"))
    author = _parse_optional_string(frontmatter.get("author"))
    license = _parse_optional_string(frontmatter.get("license"))
    allowed_tools = _parse_string_tuple(
        frontmatter.get("allowed_tools", frontmatter.get("allowed-tools"))
    )
    required_context = _parse_string_tuple(
        frontmatter.get("required_context", frontmatter.get("required-context"))
    )
    argument_hint = _parse_optional_string(
        frontmatter.get("argument-hint", frontmatter.get("argument_hint"))
    )
    context = _parse_context_value(frontmatter.get("context"))
    disable_model_invocation = bool(frontmatter.get("disable-model-invocation", False))
    user_invocable_raw = frontmatter.get("user-invocable")
    user_invocable = True if user_invocable_raw is None else bool(user_invocable_raw)
    shell_injection = bool(
        frontmatter.get("shell-injection", frontmatter.get("shell_injection", False))
    )

    return SkillMarkdownMetadata(
        name=name,
        description=description,
        declared_name=declared_name,
        description_source=description_source,
        version=version,
        tags=tags,
        author=author,
        license=license,
        allowed_tools=allowed_tools,
        required_context=required_context,
        argument_hint=argument_hint,
        context=context,
        disable_model_invocation=disable_model_invocation,
        user_invocable=user_invocable,
        shell_injection=shell_injection,
    )


def _parse_optional_string(raw: Any) -> str | None:
    if isinstance(raw, str):
        value = raw.strip()
        return value or None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = str(raw).strip()
        return value or None
    return None


def _parse_string_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple, set)):
        return tuple(
            value
            for item in raw
            if (value := _parse_optional_string(item)) is not None
        )
    return ()


def _parse_context_value(raw: Any) -> str | None:
    value = _parse_optional_string(raw)
    if value is None:
        return None
    lowered = value.lower()
    return lowered if lowered in {"inline", "fork"} else value


def _extract_skill_heading(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            return heading or None
    return None


def _extract_skill_body_description(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:200]
    return None


def _skill_skip_reason(
    metadata: SkillMarkdownMetadata,
    default_name: str,
    *,
    path: str | Path | None = None,
) -> str | None:
    resolved_path = Path(path) if path is not None else None
    if (
        resolved_path is not None
        and resolved_path.name.lower() == "skill.md"
        and metadata.declared_name is not None
        and metadata.declared_name != default_name
    ):
        return (
            f"directory name '{default_name}' does not match frontmatter name "
            f"'{metadata.declared_name}'"
        )
    if metadata.description_source == "fallback":
        return "description is empty"
    return None


def _parse_skill_frontmatter(
    content: str,
    *,
    path: str | Path | None = None,
) -> tuple[dict[str, Any], str]:
    if not content.startswith("---\n"):
        return {}, content
    marker = "\n---\n"
    end_index = content.find(marker, 4)
    if end_index == -1:
        return {}, content
    raw_frontmatter = content[4:end_index]
    body = content[end_index + len(marker) :]
    try:
        parsed = yaml.safe_load(raw_frontmatter) or {}
    except yaml.YAMLError:
        label = str(path) if path is not None else "<memory>"
        logger.debug("Failed to parse YAML frontmatter for skill %s", label, exc_info=True)
        return {}, body
    return (parsed if isinstance(parsed, dict) else {}), body
