"""Skill loading from bundled, user, compatibility, and project directories."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from openharness.config.paths import get_config_dir
from openharness.config.settings import PathRuleConfig, _default_user_skill_dirs, load_settings
from openharness.skills.bundled import get_bundled_skills
from openharness.skills.metadata import load_skill_definition
from openharness.skills.registry import SkillRegistry
from openharness.skills.types import SkillDefinition
from openharness.utils.log import get_logger

if TYPE_CHECKING:
    from openharness.config.settings import Settings

logger = get_logger(__name__)

_DEFAULT_PROJECT_SKILL_DIRS = (".openharness/skills", ".agents/skills", ".claude/skills")


def get_user_skills_dir() -> Path:
    """Return the OpenHarness user skills directory."""
    path = get_config_dir() / "skills"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_skill_dirs(settings: Settings | None = None, *, create_missing: bool = False) -> list[Path]:
    """Return user-level skill directories, using settings overrides when provided."""
    raw_dirs: list[str] = getattr(settings, "user_skill_dirs", None) or _default_user_skill_dirs()
    directories = [Path(path).expanduser().resolve() for path in raw_dirs if str(path).strip()]
    if create_missing:
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    return directories


def load_skill_registry(
    cwd: str | Path | None = None,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings=None,
) -> SkillRegistry:
    """Load bundled, user-defined, project, and plugin skills."""
    registry = SkillRegistry()
    resolved_settings = settings or load_settings()
    for skill in get_bundled_skills():
        registry.register(skill)
    for skill in load_user_skills(resolved_settings):
        registry.register(skill)
    for skill in load_skills_from_dirs(extra_skill_dirs, source="user"):
        registry.register(skill)

    if cwd is not None and getattr(resolved_settings, "allow_project_skills", True):
        project_dirs = discover_project_skill_dirs(
            cwd,
            getattr(resolved_settings, "project_skill_dirs", list(_DEFAULT_PROJECT_SKILL_DIRS)),
        )
        for skill in load_skills_from_dirs(project_dirs, source="project", create_missing=False):
            registry.register(skill)

    if cwd is not None:
        from openharness.plugins.loader import load_plugins

        for plugin in load_plugins(resolved_settings, cwd, extra_roots=extra_plugin_roots):
            if not plugin.enabled:
                continue
            for skill in plugin.skills:
                registry.register(skill)
    return registry


# ---------------------------------------------------------------------------
# Registry cache — avoids redundant file scans within a session
# ---------------------------------------------------------------------------

_cached_registry: SkillRegistry | None = None
_cached_registry_key: tuple[str | None, tuple[str, ...], tuple[str, ...], tuple[tuple[str, float, int], ...]] | None = None


def _get_registry_fingerprint(
    cwd: str | Path | None,
    extra_skill_dirs: Iterable[str | Path] | None,
    extra_plugin_roots: Iterable[str | Path] | None,
    settings,
) -> tuple[tuple[str, float, int], ...]:
    resolved_settings = settings or load_settings()
    dirs: list[Path] = []
    dirs.extend(get_user_skill_dirs(resolved_settings))
    if extra_skill_dirs:
        dirs.extend(Path(d) for d in extra_skill_dirs)
    if cwd is not None and getattr(resolved_settings, "allow_project_skills", True):
        dirs.extend(discover_project_skill_dirs(
            cwd,
            getattr(resolved_settings, "project_skill_dirs", None),
        ))

    files: list[Path] = []
    for d in dirs:
        if d.exists():
            if d.is_file():
                files.append(d)
            else:
                try:
                    files.extend(d.rglob("*.md"))
                except OSError:
                    pass

    try:
        from openharness.skills.bundled import _CONTENT_DIR
        if _CONTENT_DIR.exists():
            files.extend(_CONTENT_DIR.rglob("*.md"))
    except (ImportError, OSError):
        pass

    try:
        from openharness.plugins.loader import discover_plugin_paths_for_settings
        plugin_paths = discover_plugin_paths_for_settings(
            resolved_settings,
            cwd or Path.cwd(),
            extra_roots=extra_plugin_roots,
        )
        for p in plugin_paths:
            if p.exists():
                files.extend(p.rglob("*.json"))
                files.extend(p.rglob("*.md"))
    except Exception:
        pass

    files = sorted(set(files))
    sig: list[tuple[str, float, int]] = []
    for f in files:
        try:
            stat = f.stat()
            sig.append((str(f.resolve()), stat.st_mtime, stat.st_size))
        except OSError:
            pass
    return tuple(sig)


def load_skill_registry_cached(
    cwd: str | Path | None = None,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings=None,
) -> SkillRegistry:
    """Return a cached :class:`SkillRegistry`, rebuilding only after invalidation."""
    global _cached_registry, _cached_registry_key

    resolved_settings = settings or load_settings()
    resolved_cwd = str(Path(cwd).resolve()) if cwd else None
    dirs_key = tuple(str(Path(p).resolve()) for p in (extra_skill_dirs or ()))
    roots_key = tuple(str(Path(p).resolve()) for p in (extra_plugin_roots or ()))

    fingerprint = _get_registry_fingerprint(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=resolved_settings,
    )
    key = (resolved_cwd, dirs_key, roots_key, fingerprint)

    if _cached_registry is not None and _cached_registry_key == key:
        return _cached_registry

    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=resolved_settings,
    )
    _cached_registry = registry
    _cached_registry_key = key
    return registry


def invalidate_skill_registry_cache() -> None:
    """Clear the cached registry so the next call rebuilds from disk."""
    global _cached_registry, _cached_registry_key
    _cached_registry = None
    _cached_registry_key = None


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


def load_user_skills(settings: Settings | None = None) -> list[SkillDefinition]:
    """Load markdown skills from configured user-level skill directories."""
    return load_skills_from_dirs(get_user_skill_dirs(settings), source="user")


def discover_project_skill_dirs(
    cwd: str | Path,
    project_skill_dirs: Iterable[str | Path] | None = None,
) -> list[Path]:
    """Return existing project skill directories from cwd up to the git root.

    Directories are ordered from least-specific to most-specific so later registry
    entries can override broader project or user skills deterministically.
    """
    start = Path(cwd).expanduser().resolve()
    if not start.exists():
        start = start.parent
    if start.is_file():
        start = start.parent

    relative_dirs = _valid_project_skill_dirs(project_skill_dirs or _DEFAULT_PROJECT_SKILL_DIRS)
    git_root = _find_git_root(start)
    home = Path.home().resolve()
    current = start
    levels: list[Path] = []
    while True:
        levels.append(current)
        if git_root is not None and current == git_root:
            break
        if git_root is None and current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    roots: list[Path] = []
    seen: set[Path] = set()
    for base in reversed(levels):
        for rel in relative_dirs:
            candidate = (base / rel).resolve()
            if candidate in seen or not candidate.is_dir():
                continue
            seen.add(candidate)
            roots.append(candidate)
    return roots


def _valid_project_skill_dirs(project_skill_dirs: Iterable[str | Path]) -> list[Path]:
    """Return safe relative project skill paths."""
    paths: list[Path] = []
    for raw in project_skill_dirs:
        value = str(raw).strip()
        if not value:
            continue
        rel = Path(value)
        if rel.is_absolute() or ".." in rel.parts:
            logger.warning("Ignoring unsafe project skill dir: %s", raw)
            continue
        paths.append(rel)
    return paths


def _find_git_root(start: Path) -> Path | None:
    """Find the nearest git root containing start, if any."""
    current = start
    while True:
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            return None
        current = parent


def load_skills_from_dirs(
    directories: Iterable[str | Path] | None,
    *,
    source: str = "user",
    create_missing: bool = True,
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
        if root.exists():
            if not root.is_dir():
                continue
        elif create_missing:
            root.mkdir(parents=True, exist_ok=True)
        else:
            continue
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
