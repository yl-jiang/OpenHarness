"""Skill exports."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from openharness.skills.registry import SkillRegistry
    from openharness.skills.types import SkillDefinition

__all__ = [
    "SkillDefinition",
    "SkillRegistry",
    "discover_project_skill_dirs",
    "get_user_skill_dirs",
    "get_user_skills_dir",
    "invalidate_skill_registry_cache",
    "load_skill_registry",
    "load_skill_registry_cached",
]


def __getattr__(name: str):
    if name in {
        "discover_project_skill_dirs", "get_user_skill_dirs", "get_user_skills_dir",
        "invalidate_skill_registry_cache", "load_skill_registry", "load_skill_registry_cached",
    }:
        from openharness.skills.loader import (
            discover_project_skill_dirs,
            get_user_skill_dirs,
            get_user_skills_dir,
            invalidate_skill_registry_cache,
            load_skill_registry,
            load_skill_registry_cached,
        )

        return {
            "discover_project_skill_dirs": discover_project_skill_dirs,
            "get_user_skill_dirs": get_user_skill_dirs,
            "get_user_skills_dir": get_user_skills_dir,
            "invalidate_skill_registry_cache": invalidate_skill_registry_cache,
            "load_skill_registry": load_skill_registry,
            "load_skill_registry_cached": load_skill_registry_cached,
        }[name]
    if name == "SkillRegistry":
        from openharness.skills.registry import SkillRegistry

        return SkillRegistry
    if name == "SkillDefinition":
        from openharness.skills.types import SkillDefinition

        return SkillDefinition
    raise AttributeError(name)
