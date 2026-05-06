"""Skill loading from bundled and user directories."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openharness.config.paths import get_config_dir
from openharness.config.settings import PathRuleConfig, load_settings
from openharness.skills.bundled import get_bundled_skills
from openharness.skills.metadata import load_skill_definition
from openharness.skills.registry import SkillRegistry
from openharness.skills.types import SkillDefinition

def get_user_skills_dir() -> Path:
    """Return the user skills directory."""
    path = get_config_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_skill_registry(
    cwd: str | Path | None = None,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings=None,
) -> SkillRegistry:
    """Load bundled and user-defined skills."""
    registry = SkillRegistry()
    for skill in get_bundled_skills():
        registry.register(skill)
    for skill in load_user_skills():
        registry.register(skill)
    for skill in load_skills_from_dirs(extra_skill_dirs):
        registry.register(skill)
    if cwd is not None:
        from openharness.plugins.loader import load_plugins

        resolved_settings = settings or load_settings()
        for plugin in load_plugins(resolved_settings, cwd, extra_roots=extra_plugin_roots):
            if not plugin.enabled:
                continue
            for skill in plugin.skills:
                registry.register(skill)
    return registry


def apply_skill_path_rules(
    permission_settings,
    *,
    cwd: str | Path | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings=None,
) -> None:
    """Augment permission settings with allow rules for discovered skill directories."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    existing_patterns = {rule.pattern for rule in permission_settings.path_rules}
    for skill in registry.list_skills():
        if not skill.path:
            continue
        pattern = str((Path(skill.path).resolve().parent / "*").resolve())
        if pattern in existing_patterns:
            continue
        permission_settings.path_rules.append(PathRuleConfig(pattern=pattern, allow=True))
        existing_patterns.add(pattern)


def load_user_skills() -> list[SkillDefinition]:
    """Load markdown skills from the user config directory."""
    return load_skills_from_dirs([get_user_skills_dir()], source="user")


def load_skills_from_dirs(
    directories: Iterable[str | Path] | None,
    *,
    source: str = "user",
) -> list[SkillDefinition]:
    """Load markdown skills from one or more directories.

    Supported layout:
    - ``<root>/<skill-dir>/SKILL.md``
    """
    skills: list[SkillDefinition] = []
    if not directories:
        return skills
    seen: set[Path] = set()
    for directory in directories:
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        candidates: list[Path] = []
        for child in sorted(root.iterdir()):
            if child.is_dir():
                skill_path = child / "SKILL.md"
                if skill_path.exists():
                    candidates.append(skill_path)
        for path in candidates:
            if path in seen:
                continue
            seen.add(path)
            content = path.read_text(encoding="utf-8")
            default_name = path.parent.name
            skill = load_skill_definition(
                default_name,
                content,
                source=source,
                path=path,
            )
            if skill is not None:
                skills.append(skill)
    return skills
