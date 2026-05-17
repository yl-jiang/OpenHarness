"""Memory helpers for slash commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from openharness.memory import (
    add_memory_entry,
    get_memory_entrypoint,
    get_project_memory_dir,
    list_memory_files,
    migrate_memory,
    remove_memory_entry,
)
from openharness.memory.schema import is_disabled_metadata, is_memory_expired, split_memory_file


@dataclass(frozen=True)
class MemoryCommandBackend:
    """Storage backend used by the generic ``/memory`` slash command."""

    label: str
    default_type: str
    default_category: str
    get_memory_dir: Callable[[], Path]
    get_entrypoint: Callable[[], Path]
    list_files: Callable[[], list[Path]]
    add_entry: Callable[[str, str], Path]
    remove_entry: Callable[[str], bool]


def resolve_memory_entry_path(memory_dir: Path, candidate: str) -> tuple[Path | None, bool]:
    """Resolve a memory entry path while enforcing containment under ``memory_dir``."""

    base = memory_dir.resolve()
    resolved, invalid = _resolve_memory_candidate(base, candidate)
    if invalid:
        return None, True
    if resolved is not None and resolved.exists():
        return resolved, False
    fallback, invalid = _resolve_memory_candidate(base, f"{candidate}.md")
    if invalid:
        return None, True
    if fallback is not None and fallback.exists():
        return fallback, False
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", candidate.strip().lower()).strip("_")
    if slug and slug != candidate:
        slugged, invalid = _resolve_memory_candidate(base, f"{slug}.md")
        if invalid:
            return None, True
        if slugged is not None and slugged.exists():
            return slugged, False
    return None, False


def memory_backend_for_context(context: Any) -> MemoryCommandBackend:
    """Return the active slash-command memory backend for this command context."""

    if context.memory_backend is not None:
        return context.memory_backend
    cwd = context.cwd
    return MemoryCommandBackend(
        label="OpenHarness project memory",
        default_type="project",
        default_category="knowledge",
        get_memory_dir=lambda: get_project_memory_dir(cwd),
        get_entrypoint=lambda: get_memory_entrypoint(cwd),
        list_files=lambda: list_memory_files(cwd),
        add_entry=lambda title, content: add_memory_entry(cwd, title, content),
        remove_entry=lambda name: remove_memory_entry(cwd, name),
    )


async def handle_memory_command(args: str, context: Any) -> Any:
    from openharness.commands.core import CommandResult

    backend = memory_backend_for_context(context)
    tokens = args.split(maxsplit=1)
    if not tokens:
        return CommandResult(
            message=(
                f"Memory store: {backend.label}\n"
                f"Memory directory: {backend.get_memory_dir()}\n"
                f"Entrypoint: {backend.get_entrypoint()}"
            )
        )
    action = tokens[0]
    rest = tokens[1] if len(tokens) == 2 else ""
    if action == "list":
        memory_files = backend.list_files()
        if not memory_files:
            return CommandResult(message="No memory files.")
        return CommandResult(message="\n".join(path.name for path in memory_files))
    if action == "migrate":
        if rest not in {"--dry-run", "--apply"}:
            return CommandResult(
                message=(
                    "Usage: /memory "
                    "[list|show NAME|add TITLE :: CONTENT|remove NAME|"
                    "migrate --dry-run|migrate --apply]"
                )
            )
        summary = migrate_memory(
            context.cwd if hasattr(context, "cwd") else ".",
            memory_dir=backend.get_memory_dir(),
            default_type=backend.default_type,
            default_category=backend.default_category,
            apply=rest == "--apply",
        )
        mode = "dry run" if summary.dry_run else "applied"
        lines = [
            f"Memory migration {mode}.",
            f"Scanned: {summary.scanned}",
            f"Changed: {summary.changed}",
            f"Unchanged: {summary.unchanged}",
            f"Failed: {summary.failed}",
        ]
        if summary.backup_dir:
            lines.append(f"Backup: {summary.backup_dir}")
        if summary.changed_files:
            lines.append("Changed files: " + ", ".join(summary.changed_files))
        if summary.failed_files:
            lines.append("Failed files: " + ", ".join(summary.failed_files))
        return CommandResult(message="\n".join(lines))
    if action == "show" and rest:
        memory_dir = backend.get_memory_dir()
        path, invalid = resolve_memory_entry_path(memory_dir, rest)
        if invalid:
            return CommandResult(message="Memory entry path must stay within the configured memory directory.")
        if path is None:
            return CommandResult(message=f"Memory entry not found: {rest}")
        if not path.exists():
            return CommandResult(message=f"Memory entry not found: {rest}")
        content = path.read_text(encoding="utf-8")
        metadata, _, _, _ = split_memory_file(content)
        if is_disabled_metadata(metadata) or is_memory_expired(metadata):
            return CommandResult(message=f"Memory entry not found: {rest}")
        return CommandResult(message=content)
    if action == "add" and rest:
        title, separator, content = rest.partition("::")
        if not separator or not title.strip() or not content.strip():
            return CommandResult(message="Usage: /memory add TITLE :: CONTENT")
        path = backend.add_entry(title.strip(), content.strip())
        return CommandResult(message=f"Added memory entry {path.name}")
    if action == "remove" and rest:
        if backend.remove_entry(rest.strip()):
            return CommandResult(message=f"Removed memory entry {rest.strip()}")
        return CommandResult(message=f"Memory entry not found: {rest.strip()}")
    return CommandResult(
        message=(
            "Usage: /memory "
            "[list|show NAME|add TITLE :: CONTENT|remove NAME|"
            "migrate --dry-run|migrate --apply]"
        )
    )


def _resolve_memory_candidate(memory_dir: Path, candidate: str) -> tuple[Path | None, bool]:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = memory_dir / path
    resolved = path.resolve()
    try:
        resolved.relative_to(memory_dir)
    except ValueError:
        return None, True
    return resolved, False
