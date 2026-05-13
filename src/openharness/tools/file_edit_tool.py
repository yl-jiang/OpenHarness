"""String-based file editing tool with conflict detection."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.engine.types import ToolMetadataKey
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_CACHE_KEY = ToolMetadataKey.FILE_READ_CACHE.value


class FileEditToolInput(BaseModel):
    """Arguments for the file edit tool."""

    path: str = Field(description="Path of the file to edit")
    old_str: str = Field(description="Existing text to replace")
    new_str: str = Field(description="Replacement text")
    replace_all: bool = Field(default=False)


class FileEditTool(BaseTool):
    """Replace text in an existing file."""

    name = "edit_file"
    description = (
        "Edit an existing file by replacing a string. "
        "old_str must appear exactly once (use replace_all=true to replace every occurrence). "
        "Read the file first with read_file to avoid stale-content conflicts."
    )
    input_model = FileEditToolInput

    async def compute_preview(
        self, arguments: FileEditToolInput, cwd: Path  # type: ignore[override]
    ) -> tuple[str, int, int] | None:
        path = _resolve_path(cwd, arguments.path)
        if not path.exists():
            return None
        original = path.read_text(encoding="utf-8")
        if arguments.old_str not in original:
            return None
        if arguments.replace_all:
            updated = original.replace(arguments.old_str, arguments.new_str)
        else:
            updated = original.replace(arguments.old_str, arguments.new_str, 1)
        return _compute_diff(str(path), original, updated)

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to edit",
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Existing text to replace",
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text",
                    },
                    "replace_all": {
                        "type": "boolean",
                        "description": "Replace all occurrences of old_str; default false replaces only the first",
                        "default": False,
                    },
                },
                "required": ["path", "old_str", "new_str"],
            },
        }

    async def execute(
        self,
        arguments: FileEditToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from openharness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from openharness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        if not path.exists():
            return ToolResult(output=f"File not found: {path}", is_error=True)

        # --- conflict detection -------------------------------------------
        conflict = _check_edit_conflict(context.metadata, path)
        if conflict:
            return ToolResult(output=conflict, is_error=True)
        # -----------------------------------------------------------------

        original = path.read_text(encoding="utf-8")
        if arguments.old_str not in original:
            return ToolResult(output="old_str was not found in the file", is_error=True)

        if arguments.replace_all:
            updated = original.replace(arguments.old_str, arguments.new_str)
        else:
            updated = original.replace(arguments.old_str, arguments.new_str, 1)

        _, added, removed = _compute_diff(str(path), original, updated)
        path.write_text(updated, encoding="utf-8")
        stats = f"  ({_ANSI_GREEN}+{added}{_ANSI_RESET} {_ANSI_RED}-{removed}{_ANSI_RESET})"
        return ToolResult(output=f"Updated {path}{stats}")


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _compute_diff(filename: str, original: str, updated: str) -> tuple[str, int, int]:
    """Return (unified_diff_text, added_lines, removed_lines)."""
    diff_lines = list(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=filename,
            tofile=filename,
            lineterm="",
        )
    )
    added = sum(1 for ln in diff_lines if ln.startswith("+") and not ln.startswith("+++"))
    removed = sum(1 for ln in diff_lines if ln.startswith("-") and not ln.startswith("---"))
    return "".join(diff_lines), added, removed


_ANSI_GREEN = "\033[32m"
_ANSI_RED = "\033[31m"
_ANSI_RESET = "\033[0m"


def _check_edit_conflict(metadata: dict[str, Any], path: Path) -> str | None:
    """Return an error message if the file was modified since it was last read.

    Returns ``None`` when no conflict is detected (including when the file was
    never read via ``read_file``, in which case there is no baseline to compare).
    """
    cache = metadata.get(_CACHE_KEY) if metadata else None
    if not isinstance(cache, dict):
        return None
    entry = cache.get(str(path))
    if not isinstance(entry, dict):
        return None  # File was never read; no baseline, allow the edit.

    try:
        current_mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None  # Cannot stat; let the edit proceed.

    cached_mtime_ns = entry.get("mtime_ns")
    if cached_mtime_ns is not None and current_mtime_ns != cached_mtime_ns:
        return (
            f"Edit conflict: {path} was modified externally since it was last read. "
            "Please re-read the file with read_file before editing."
        )
    return None
