"""Skill command helpers."""

from __future__ import annotations

import re
from typing import Any

from openharness.commands.core import CommandContext, CommandResult
from openharness.engine.types import ToolMetadataKey
from openharness.permissions import PermissionChecker
from openharness.skills import load_skill_registry
from openharness.skills.loader import apply_skill_path_rules

_SKILL_ARG_REGEX = re.compile(r"""(?:\[Image\s+\d+\]|"[^"]*"|'[^']*'|[^\s"']+)""")
_SKILL_PLACEHOLDER_REGEX = re.compile(r"\$(\d+)")


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


def _is_user_invocable_skill(skill: Any) -> bool:
    return bool(getattr(skill, "user_invocable", True))


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


def resolve_skill_alias_command(raw_input: str, context: CommandContext) -> CommandResult | None:
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
    return CommandResult(
        message=f"Loaded skill: {skill.name}",
        submit_prompt=render_skill_load_prompt(skill, args),
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
        lines = ["Available skills:"]
        for skill in skills:
            source = f" [{skill.source}]"
            lines.append(f"- {skill.name}{source}: {skill.description}")
        return CommandResult(message="\n".join(lines))

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
    return CommandResult(
        message=f"Loaded skill: {skill.name}",
        submit_prompt=render_skill_load_prompt(skill, load_args),
    )
