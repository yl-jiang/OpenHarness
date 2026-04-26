"""File reading tool with per-span content cache."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.engine.types import ToolMetadataKey
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_CACHE_KEY = ToolMetadataKey.FILE_READ_CACHE.value


class FileReadToolInput(BaseModel):
    """Arguments for the file read tool."""

    path: str = Field(description="Path of the file to read")
    offset: int = Field(default=0, ge=0, description="Zero-based starting line")
    limit: int = Field(default=200, ge=1, le=2000, description="Number of lines to return")


class FileReadTool(BaseTool):
    """Read a UTF-8 text file with line numbers."""

    name = "read_file"
    description = (
        "Read a UTF-8 text file from the local repository with line numbers. "
        "Use offset and limit for paginated reading of large files."
    )
    input_model = FileReadToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to read",
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Zero-based starting line",
                        "default": 0,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of lines to return (1-2000)",
                        "default": 200,
                    },
                },
                "required": ["path"],
            },
        }

    def is_read_only(self, arguments: FileReadToolInput) -> bool:
        del arguments
        return True

    async def execute(
        self,
        arguments: FileReadToolInput,
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
        if path.is_dir():
            return ToolResult(output=f"Cannot read directory: {path}", is_error=True)

        stat = path.stat()
        mtime_ns: int = stat.st_mtime_ns

        # --- cache check ---------------------------------------------------
        span = (arguments.offset, arguments.limit)
        abs_path_str = str(path)
        cache = _get_cache(context.metadata)
        cached_entry = cache.get(abs_path_str)

        if (
            cached_entry is not None
            and cached_entry.get("mtime_ns") == mtime_ns
            and span in cached_entry.get("spans", {})
        ):
            return ToolResult(
                output=f"[file content unchanged: {path} lines {arguments.offset + 1}-{arguments.offset + arguments.limit}]"
            )
        # -------------------------------------------------------------------

        raw = path.read_bytes()
        if b"\x00" in raw:
            return ToolResult(output=f"Binary file cannot be read as text: {path}", is_error=True)

        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        selected = lines[arguments.offset : arguments.offset + arguments.limit]
        numbered = [
            f"{arguments.offset + index + 1:>6}\t{line}"
            for index, line in enumerate(selected)
        ]
        if not numbered:
            return ToolResult(output=f"(no content in selected range for {path})")

        # --- update cache --------------------------------------------------
        _update_cache(cache, abs_path_str, mtime_ns=mtime_ns, span=span)
        # -------------------------------------------------------------------

        return ToolResult(output="\n".join(numbered))


def _resolve_path(base: Path, candidate: str) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def _get_cache(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return (and lazily create) the file-read cache from tool metadata."""
    if not metadata:
        return {}
    entry = metadata.get(_CACHE_KEY)
    if not isinstance(entry, dict):
        entry = {}
        metadata[_CACHE_KEY] = entry
    return entry


def _update_cache(
    cache: dict[str, Any],
    abs_path: str,
    *,
    mtime_ns: int,
    span: tuple[int, int],
) -> None:
    """Record that *span* was read from *abs_path* at *mtime_ns*."""
    existing = cache.get(abs_path)
    if isinstance(existing, dict) and existing.get("mtime_ns") == mtime_ns:
        existing.setdefault("spans", {})[span] = True
    else:
        cache[abs_path] = {"mtime_ns": mtime_ns, "spans": {span: True}}
