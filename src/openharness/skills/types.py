"""Skill data models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillDefinition:
    """A loaded skill."""

    name: str
    description: str
    content: str
    source: str
    path: str | None = None
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
