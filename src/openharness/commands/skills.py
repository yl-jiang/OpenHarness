"""Skill command helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openharness.commands.core import CommandContext, CommandResult
from openharness.engine.types import ToolMetadataKey
from openharness.permissions import PermissionChecker
from openharness.skills import load_skill_registry
from openharness.skills.loader import apply_skill_path_rules
from openharness.skills.shell_injection import (
    SkillShellInjectionError,
    extract_injections,
    render_skill_prompt_with_shell,
)

_SKILL_ARG_REGEX = re.compile(r"""(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)""")
_SKILL_PLACEHOLDER_REGEX = re.compile(r"\$(\d+)")
_SOURCE_ORDER = ("project", "user", "plugin", "bundled")
_SOURCE_LABELS = {
    "project": "Project skills",
    "user": "User skills",
    "plugin": "Plugin skills",
    "bundled": "Bundled skills",
}


def _tokenize_skill_arguments(raw_args: str) -> list[str]:
    tokens = _SKILL_ARG_REGEX.findall(raw_args)
    return [re.sub(r'^["\']|["\']$', "", token) for token in tokens]


def render_skill_template(template: str, args: str) -> str:
    raw_args = args.strip()
    tokens = _tokenize_skill_arguments(raw_args)
    placeholders = [int(match) for match in _SKILL_PLACEHOLDER_REGEX.findall(template)]
    last_placeholder = max(placeholders, default=0)

    def replace_placeholder(match: re.Match[str]) -> str:
        position = int(match.group(1))
        index = position - 1
        if index >= len(tokens):
            return ""
        if position == last_placeholder:
            return " ".join(tokens[index:])
        return tokens[index]

    rendered = _SKILL_PLACEHOLDER_REGEX.sub(replace_placeholder, template)
    uses_arguments_placeholder = "${ARGUMENTS}" in template or "$ARGUMENTS" in template
    rendered = rendered.replace("${ARGUMENTS}", raw_args).replace("$ARGUMENTS", raw_args)
    if not placeholders and not uses_arguments_placeholder and raw_args:
        rendered = f"{rendered}\n\n{raw_args}"
    return rendered


def render_skill_load_prompt(skill: Any, args: str) -> str:
    return render_skill_template(skill.content, args)


async def _render_skill_prompt(
    skill: Any,
    args: str,
    *,
    context: CommandContext,
) -> str:
    """Render a skill prompt, expanding ``!{cmd}`` injections when enabled.

    Skills without shell injection fall through to the cheap synchronous
    template path so the existing test surface and behaviour are preserved.
    Skills containing ``!{`` without ``shell-injection: true`` raise
    :class:`SkillShellInjectionError` rather than silently leaking the
    template through to the model.
    """

    segments = extract_injections(skill.content)
    has_shell = any(seg.kind == "shell" for seg in segments)
    if not has_shell:
        return render_skill_load_prompt(skill, args)
    return await render_skill_prompt_with_shell(skill, args, context=context)


def _is_user_invocable_skill(skill: Any) -> bool:
    return bool(getattr(skill, "user_invocable", True))


def _format_skill_path(path: str | None, cwd: str | Path) -> str | None:
    if not path:
        return None
    resolved = Path(path).expanduser().resolve()
    try:
        return str(resolved.relative_to(Path(cwd).expanduser().resolve()))
    except ValueError:
        pass
    try:
        return f"~/{resolved.relative_to(Path.home().resolve())}"
    except ValueError:
        return str(resolved)


def _format_skills_list(skills: list[Any], cwd: str | Path) -> str:
    grouped: dict[str, list[Any]] = {}
    for skill in skills:
        grouped.setdefault(str(skill.source), []).append(skill)

    ordered_sources = [source for source in _SOURCE_ORDER if source in grouped]
    ordered_sources.extend(sorted(source for source in grouped if source not in _SOURCE_ORDER))

    lines: list[str] = [f"Skills ({len(skills)})", ""]

    for source in ordered_sources:
        source_skills = grouped[source]
        label = _SOURCE_LABELS.get(source, source.title() + " skills")
        lines.append(f"{label} ({len(source_skills)})")
        lines.append("-" * 48)
        for skill in source_skills:
            desc = skill.description or "No description"
            lines.append(f"/{skill.name}")
            lines.append(f"  description: {desc}")
            display_path = _format_skill_path(skill.path, cwd)
            if display_path:
                lines.append(f"  path: {display_path}")
            lines.append("")

    lines.append("Use /skills show NAME for full content.")
    return "\n".join(lines)


def remember_loaded_skill(context: CommandContext, name: str) -> None:
    bucket = context.engine.tool_metadata.setdefault(ToolMetadataKey.INVOKED_SKILLS.value, [])
    if not isinstance(bucket, list):
        bucket = []
        context.engine.tool_metadata[ToolMetadataKey.INVOKED_SKILLS.value] = bucket
    if name in bucket:
        bucket.remove(name)
    bucket.append(name)


def build_permission_checker(settings: Any, context: CommandContext) -> PermissionChecker:
    apply_skill_path_rules(
        settings.permission,
        cwd=context.cwd,
        extra_skill_dirs=context.extra_skill_dirs,
        extra_plugin_roots=context.extra_plugin_roots,
        settings=settings,
    )
    return PermissionChecker(settings.permission)


async def resolve_skill_alias_command(
    raw_input: str, context: CommandContext
) -> CommandResult | None:
    """Resolve ``/<skill-name> ...`` as a direct skill invocation."""

    if not raw_input.startswith("/"):
        return None
    name, _, args = raw_input[1:].partition(" ")
    skill_name = name.strip()
    if not skill_name or skill_name == "skills":
        return None
    registry = load_skill_registry(
        context.cwd,
        extra_skill_dirs=context.extra_skill_dirs,
        extra_plugin_roots=context.extra_plugin_roots,
    )
    skill = registry.get(skill_name)
    if skill is None or not _is_user_invocable_skill(skill):
        return None
    remember_loaded_skill(context, skill.name)
    try:
        prompt = await _render_skill_prompt(skill, args, context=context)
    except SkillShellInjectionError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(
        message=f"Loaded skill: {skill.name}",
        submit_prompt=prompt,
    )


async def handle_skills_command(args: str, context: CommandContext) -> CommandResult:
    skill_registry = load_skill_registry(
        context.cwd,
        extra_skill_dirs=context.extra_skill_dirs,
        extra_plugin_roots=context.extra_plugin_roots,
    )
    tokens = args.split(maxsplit=2)
    if not tokens or tokens[0] == "list":
        skills = [skill for skill in skill_registry.list_skills() if _is_user_invocable_skill(skill)]
        if not skills:
            return CommandResult(message="No skills available.")
        return CommandResult(message=_format_skills_list(skills, context.cwd))

    if tokens[0] == "show":
        if len(tokens) < 2:
            return CommandResult(message="Usage: /skills show NAME")
        skill = skill_registry.get(tokens[1])
        if skill is None or not _is_user_invocable_skill(skill):
            return CommandResult(message=f"Skill not found: {tokens[1]}")
        return CommandResult(message=skill.content)

    name, _, load_args = args.partition(" ")
    skill = skill_registry.get(name)
    if skill is None or not _is_user_invocable_skill(skill):
        return CommandResult(message=f"Skill not found: {name}")
    remember_loaded_skill(context, skill.name)
    try:
        prompt = await _render_skill_prompt(skill, load_args, context=context)
    except SkillShellInjectionError as exc:
        return CommandResult(message=str(exc))
    return CommandResult(
        message=f"Loaded skill: {skill.name}",
        submit_prompt=prompt,
    )
