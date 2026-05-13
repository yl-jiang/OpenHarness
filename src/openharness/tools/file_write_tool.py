"""File writing tool."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class FileWriteToolInput(BaseModel):
    """Arguments for the file write tool."""

    path: str = Field(description="Path of the file to write")
    content: str = Field(description="Full file contents")
    create_directories: bool = Field(default=True)


class FileWriteTool(BaseTool):
    """Write complete file contents."""

    name = "write_file"
    description = "Create or overwrite a text file in the local repository."
    input_model = FileWriteToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path of the file to write",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file contents",
                    },
                    "create_directories": {
                        "type": "boolean",
                        "description": "Create parent directories if they do not exist",
                        "default": True,
                    },
                },
                "required": ["path", "content"],
            },
        }

    async def execute(
        self,
        arguments: FileWriteToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        path = _resolve_path(context.cwd, arguments.path)

        from openharness.sandbox.session import is_docker_sandbox_active

        if is_docker_sandbox_active():
            from openharness.sandbox.path_validator import validate_sandbox_path

            allowed, reason = validate_sandbox_path(path, context.cwd)
            if not allowed:
                return ToolResult(output=f"Sandbox: {reason}", is_error=True)

        approval_prompt = context.metadata.get("edit_approval_prompt") if context.metadata else None
        if approval_prompt is not None:
            original = path.read_text(encoding="utf-8") if path.exists() else ""
            diff_text, added, removed = _compute_diff(str(path), original, arguments.content)
            reply = await approval_prompt(str(path), diff_text, added, removed)
            if reply == "reject":
                return ToolResult(output=f"Write rejected by user: {path}", is_error=True)
            if arguments.create_directories:
                path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(arguments.content, encoding="utf-8")
            stats = f"  ({_ANSI_GREEN}+{added}{_ANSI_RESET} {_ANSI_RED}-{removed}{_ANSI_RESET})"
            return ToolResult(output=f"Wrote {path}{stats}")

        if arguments.create_directories:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(arguments.content, encoding="utf-8")
        return ToolResult(output=f"Wrote {path}")


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
