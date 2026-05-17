"""Memory helpers for slash commands."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from openharness.config.settings import load_settings
from openharness.memory import (
    add_memory_entry,
    get_memory_entrypoint,
    get_project_memory_dir,
    list_memory_files,
    migrate_memory,
    remove_memory_entry,
    scan_memory_files,
)
from openharness.memory.agent import (
    AgentMemoryScope,
    ensure_agent_memory_vault,
    get_agent_memory_entrypoint,
    initialize_agent_memory_from_snapshot,
)
from openharness.memory.schema import (
    DEFAULT_MEMORY_SCOPE,
    DEFAULT_MEMORY_TYPE,
    MEMORY_TYPES,
    MemoryScope,
    MemoryType,
    is_disabled_metadata,
    is_memory_expired,
    parse_memory_scope,
    parse_memory_type,
    split_memory_file,
)
from openharness.memory.team import (
    check_team_memory_secrets,
    ensure_team_memory_vault,
    get_team_memory_dir,
)
from openharness.services.memory_extract import extract_memories_from_turn
from openharness.services.session_memory import (
    get_session_memory_content,
    get_session_memory_path,
    update_session_memory_file,
)


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
                f"Entrypoint: {backend.get_entrypoint()}\n"
                "Commands: list, show NAME, add TITLE :: CONTENT, remove NAME, "
                "edit [NAME], validate, extract, session, team, agent, "
                "migrate --dry-run, migrate --apply"
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
                    "edit [NAME]|validate|extract|session|team|agent|"
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
        memory_type, scope, cleaned_rest = _parse_memory_add_flags(rest)
        title, separator, content = cleaned_rest.partition("::")
        if not separator or not title.strip() or not content.strip():
            return CommandResult(message="Usage: /memory add [--type TYPE] [--scope SCOPE] TITLE :: CONTENT")
        if context.memory_backend is None:
            try:
                path = add_memory_entry(
                    context.cwd,
                    title.strip(),
                    content.strip(),
                    memory_type=memory_type,
                    scope=scope,
                )
            except ValueError as exc:
                return CommandResult(message=str(exc))
        else:
            path = backend.add_entry(title.strip(), content.strip())
        return CommandResult(message=f"Added memory entry {path.name}")
    if action == "remove" and rest:
        if backend.remove_entry(rest.strip()):
            return CommandResult(message=f"Removed memory entry {rest.strip()}")
        return CommandResult(message=f"Memory entry not found: {rest.strip()}")
    if action == "edit":
        return _handle_memory_edit_command(rest, context, backend)
    if action == "validate":
        return _handle_memory_validate_command(context)
    if action == "extract":
        if context.memory_backend is not None:
            return CommandResult(message="Memory extraction is only supported for OpenHarness project memory.")
        result = await extract_memories_from_turn(
            cwd=context.cwd,
            api_client=context.engine.api_client,
            model=context.engine.model,
            messages=context.engine.messages,
            max_records=load_settings().memory.auto_extract_max_records,
        )
        if result.skipped:
            return CommandResult(message=f"Memory extraction skipped: {result.reason}")
        return CommandResult(
            message="Memory extraction wrote:\n" + "\n".join(f"- {path}" for path in result.written_paths)
        )
    if action == "session":
        return _handle_memory_session_command(rest, context)
    if action == "team":
        return _handle_memory_team_command(rest, context)
    if action == "agent":
        return _handle_memory_agent_command(rest, context)
    return CommandResult(
        message=(
            "Usage: /memory "
            "[list|show NAME|add TITLE :: CONTENT|remove NAME|edit [NAME]|"
            "validate|extract|session|team|agent|"
            "migrate --dry-run|migrate --apply]"
        )
    )


def _handle_memory_edit_command(
    args: str,
    context: Any,
    backend: MemoryCommandBackend,
) -> Any:
    from openharness.commands.core import CommandResult

    memory_dir = backend.get_memory_dir()
    target = backend.get_entrypoint()
    if args.strip():
        path, invalid = resolve_memory_entry_path(memory_dir, args.strip())
        if invalid:
            return CommandResult(message="Memory entry path must stay within the configured memory directory.")
        if path is None:
            return CommandResult(message=f"Memory entry not found: {args.strip()}")
        target = path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        return CommandResult(message=f"Memory file ready: {target}\nSet $VISUAL or $EDITOR to open it from /memory edit.")
    result = subprocess.run([editor, str(target)], cwd=context.cwd, check=False)
    if result.returncode != 0:
        return CommandResult(message=f"Editor exited with status {result.returncode}: {editor}")
    return CommandResult(message=f"Edited memory file: {target}")


def _parse_memory_add_flags(args: str) -> tuple[MemoryType, MemoryScope, str]:
    """Parse optional ``/memory add`` type/scope flags."""

    memory_type = DEFAULT_MEMORY_TYPE
    scope = DEFAULT_MEMORY_SCOPE
    rest = args.strip()
    changed = True
    while changed:
        changed = False
        if rest.startswith("--type "):
            _, _, tail = rest.partition(" ")
            raw, _, rest = tail.partition(" ")
            parsed = parse_memory_type(raw, default=DEFAULT_MEMORY_TYPE)
            if parsed is not None:
                memory_type = parsed
            changed = True
        if rest.startswith("--scope "):
            _, _, tail = rest.partition(" ")
            raw, _, rest = tail.partition(" ")
            parsed_scope = parse_memory_scope(raw, default=DEFAULT_MEMORY_SCOPE)
            if parsed_scope is not None:
                scope = parsed_scope
            changed = True
    return memory_type, scope, rest


def _handle_memory_validate_command(context: Any) -> Any:
    from openharness.commands.core import CommandResult

    memory_dir = get_project_memory_dir(context.cwd)
    headers = scan_memory_files(context.cwd, max_files=500)
    issues: list[str] = []
    for header in headers:
        raw_type = header.frontmatter.get("type") or header.frontmatter.get("memory_type")
        if parse_memory_type(raw_type) is None:
            issues.append(
                f"- {header.relative_path}: invalid or missing type {raw_type!r}; expected {', '.join(MEMORY_TYPES)}"
            )
        if "team" in Path(header.relative_path).parts:
            try:
                text = header.path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                text = ""
            secret_error = check_team_memory_secrets(text)
            if secret_error:
                issues.append(f"- {header.relative_path}: {secret_error}")
    if not issues:
        return CommandResult(
            message=(
                "Memory validation passed.\n"
                f"- files: {len(headers)}\n"
                f"- memory_dir: {memory_dir}"
            )
        )
    return CommandResult(message="Memory validation issues:\n" + "\n".join(issues))


def _handle_memory_session_command(args: str, context: Any) -> Any:
    from openharness.commands.core import CommandResult

    action = args.split(maxsplit=1)[0] if args.strip() else "status"
    path = get_session_memory_path(context.cwd, context.session_id or "default")
    if action == "update":
        path = update_session_memory_file(
            context.cwd,
            context.engine.messages,
            tool_metadata=context.engine.tool_metadata,
            session_id=context.session_id or "default",
        )
        return CommandResult(message=f"Updated session memory: {path}")
    if action == "show":
        content = get_session_memory_content(path)
        return CommandResult(message=content or f"No session memory at {path}")
    return CommandResult(
        message=(
            "Session memory:\n"
            f"- path: {path}\n"
            f"- exists: {path.exists()}\n"
            "Commands: /memory session [status|show|update]"
        )
    )


def _handle_memory_team_command(args: str, context: Any) -> Any:
    from openharness.commands.core import CommandResult

    action = args.split(maxsplit=1)[0] if args.strip() else "status"
    team_dir = ensure_team_memory_vault(context.cwd)
    if action == "list":
        files = sorted(path for path in team_dir.rglob("*.md") if path.name != "MEMORY.md")
        return CommandResult(message="\n".join(str(path.relative_to(team_dir)) for path in files) or "No team memory files.")
    if action == "validate":
        issues: list[str] = []
        for path in sorted(team_dir.rglob("*.md")):
            if path.name == "MEMORY.md":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            secret_error = check_team_memory_secrets(text)
            if secret_error:
                issues.append(f"- {path.relative_to(team_dir)}: {secret_error}")
        return CommandResult(message="Team memory validation passed." if not issues else "\n".join(issues))
    return CommandResult(
        message=(
            "Team memory:\n"
            f"- directory: {get_team_memory_dir(context.cwd)}\n"
            f"- exists: {team_dir.exists()}\n"
            "Commands: /memory team [status|list|validate]"
        )
    )


def _handle_memory_agent_command(args: str, context: Any) -> Any:
    from openharness.commands.core import CommandResult

    parts = args.split()
    action = parts[0] if parts else "status"
    agent_type = parts[1] if len(parts) > 1 else "default"
    raw_scope = parts[2] if len(parts) > 2 else "project"
    if raw_scope not in {"user", "project", "local"}:
        return CommandResult(message="Agent memory scope must be one of: user, project, local")
    scope = cast(AgentMemoryScope, raw_scope)
    if action == "snapshot":
        target = initialize_agent_memory_from_snapshot(context.cwd, agent_type, scope)
        return CommandResult(message=f"Initialized agent memory from snapshot: {target}" if target else "No snapshot found.")
    vault = ensure_agent_memory_vault(context.cwd, agent_type, scope)
    return CommandResult(
        message=(
            "Agent memory:\n"
            f"- agent_type: {agent_type}\n"
            f"- scope: {scope}\n"
            f"- directory: {vault}\n"
            f"- entrypoint: {get_agent_memory_entrypoint(context.cwd, agent_type, scope)}"
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
