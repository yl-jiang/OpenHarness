"""File and git diff tool."""

from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult


class DiffToolInput(BaseModel):
    """Arguments for the diff tool."""

    operation: Literal["file_diff", "git_diff"] = Field(
        description=(
            "'file_diff': unified diff between two local files. "
            "'git_diff': git diff optionally filtered by path and/or refs."
        )
    )
    path_a: str | None = Field(default=None, description="First file path (for file_diff)")
    path_b: str | None = Field(default=None, description="Second file path (for file_diff)")
    path: str | None = Field(
        default=None,
        description="File or directory path to scope git_diff",
    )
    ref: str | None = Field(
        default=None,
        description="Base ref for git_diff; if omitted, compares working tree to index",
    )
    ref2: str | None = Field(
        default=None,
        description="Compare ref for git_diff — produces ref..ref2 diff",
    )
    staged: bool = Field(default=False, description="Show staged (--cached) diff in git_diff")
    context_lines: int = Field(
        default=3,
        ge=0,
        le=15,
        description="Lines of context to include around each change",
    )


class DiffTool(BaseTool):
    """Compare two files or show git diffs."""

    name = "diff"
    description = (
        "Compare two local files (file_diff) or run a git diff "
        "optionally scoped to a path and refs (git_diff)."
    )
    input_model = DiffToolInput

    def is_read_only(self, arguments: DiffToolInput) -> bool:
        del arguments
        return True

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": ["file_diff", "git_diff"],
                        "description": (
                            "'file_diff': compare path_a vs path_b. "
                            "'git_diff': run git diff."
                        ),
                    },
                    "path_a": {
                        "type": "string",
                        "description": "First file for file_diff",
                    },
                    "path_b": {
                        "type": "string",
                        "description": "Second file for file_diff",
                    },
                    "path": {
                        "type": "string",
                        "description": "File/dir scope for git_diff",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Base ref for git_diff",
                    },
                    "ref2": {
                        "type": "string",
                        "description": "Compare ref for git_diff (ref..ref2)",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged changes",
                        "default": False,
                    },
                    "context_lines": {
                        "type": "integer",
                        "description": "Context lines around changes (0-15)",
                        "default": 3,
                    },
                },
                "required": ["operation"],
            },
        }

    async def execute(
        self,
        arguments: DiffToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        if arguments.operation == "file_diff":
            return _file_diff(arguments, context.cwd)
        if arguments.operation == "git_diff":
            return _git_diff(arguments, context.cwd)
        return ToolResult(output=f"Unknown operation: {arguments.operation}", is_error=True)


def _resolve(base: Path, candidate: str | None) -> Path | None:
    if candidate is None:
        return None
    p = Path(candidate).expanduser()
    if not p.is_absolute():
        p = base / p
    return p.resolve()


def _file_diff(args: DiffToolInput, cwd: Path) -> ToolResult:
    path_a = _resolve(cwd, args.path_a)
    path_b = _resolve(cwd, args.path_b)

    if path_a is None or path_b is None:
        return ToolResult(
            output="file_diff requires both path_a and path_b", is_error=True
        )
    for p in (path_a, path_b):
        if not p.exists():
            return ToolResult(output=f"File not found: {p}", is_error=True)
        if p.is_dir():
            return ToolResult(output=f"Cannot diff a directory: {p}", is_error=True)

    try:
        lines_a = path_a.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        lines_b = path_b.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    except OSError as exc:
        return ToolResult(output=f"Could not read file: {exc}", is_error=True)

    diff = list(
        difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=str(path_a),
            tofile=str(path_b),
            n=args.context_lines,
        )
    )
    if not diff:
        return ToolResult(output="Files are identical.")
    return ToolResult(output="".join(diff))


def _git_diff(args: DiffToolInput, cwd: Path) -> ToolResult:
    cmd = ["git", "diff", f"--unified={args.context_lines}"]

    if args.staged:
        cmd.append("--cached")
    if args.ref and args.ref2:
        cmd.append(f"{args.ref}..{args.ref2}")
    elif args.ref:
        cmd.append(args.ref)
    if args.path:
        cmd.extend(["--", args.path])

    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return ToolResult(output="git diff timed out", is_error=True)
    except FileNotFoundError:
        return ToolResult(output="git is not installed or not in PATH", is_error=True)

    combined = (proc.stdout + proc.stderr).strip()
    if proc.returncode != 0:
        return ToolResult(output=combined or f"git diff exited with code {proc.returncode}", is_error=True)
    return ToolResult(output=combined or "No differences.")
