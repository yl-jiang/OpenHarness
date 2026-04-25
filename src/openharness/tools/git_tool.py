"""Git operations tool."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult

_READ_ONLY_OPS: frozenset[str] = frozenset(
    {"status", "diff", "log", "show", "branch_list", "stash_list"}
)

_TIMEOUT = 30


class GitToolInput(BaseModel):
    """Arguments for the git tool."""

    operation: Literal[
        "status",
        "diff",
        "log",
        "show",
        "branch_list",
        "branch_create",
        "branch_delete",
        "stash_list",
        "stash_push",
        "stash_pop",
        "commit",
        "add",
        "checkout",
    ] = Field(description="Git operation to perform")
    path: str | None = Field(
        default=None,
        description="File or directory path scoping the operation",
    )
    ref: str | None = Field(
        default=None,
        description="Git ref (branch name, commit SHA, tag). Used as the base ref for 'diff'.",
    )
    ref2: str | None = Field(
        default=None,
        description="Second git ref. When set together with 'ref', 'diff' compares ref..ref2.",
    )
    message: str | None = Field(default=None, description="Commit message for 'commit'")
    max_count: int = Field(
        default=10,
        ge=1,
        le=200,
        description="Maximum number of log entries to return",
    )
    all_files: bool = Field(
        default=False,
        description="For 'commit': stage all tracked changes (-a). For 'add': stage all changes.",
    )
    staged: bool = Field(default=False, description="For 'diff': show staged (--cached) changes")
    name: str | None = Field(
        default=None,
        description="Branch name for 'branch_create', 'branch_delete', or 'checkout'",
    )
    stash_message: str | None = Field(
        default=None, description="Optional message for 'stash_push'"
    )


class GitTool(BaseTool):
    """Execute git operations with structured output."""

    name = "git"
    description = (
        "Run git operations: status, diff, log, show, branch management, "
        "stash, commit, add, and checkout."
    )
    input_model = GitToolInput

    def is_read_only(self, arguments: GitToolInput) -> bool:
        return arguments.operation in _READ_ONLY_OPS

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "status",
                            "diff",
                            "log",
                            "show",
                            "branch_list",
                            "branch_create",
                            "branch_delete",
                            "stash_list",
                            "stash_push",
                            "stash_pop",
                            "commit",
                            "add",
                            "checkout",
                        ],
                        "description": "Git operation to perform",
                    },
                    "path": {
                        "type": "string",
                        "description": "File or directory path scoping the operation",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Git ref (branch, commit, tag); base ref for 'diff'",
                    },
                    "ref2": {
                        "type": "string",
                        "description": "Second ref for 'diff ref..ref2' comparisons",
                    },
                    "message": {"type": "string", "description": "Commit message"},
                    "max_count": {
                        "type": "integer",
                        "description": "Max log entries (1-200)",
                        "default": 10,
                    },
                    "all_files": {
                        "type": "boolean",
                        "description": "Stage all changes for 'commit'/'add'",
                        "default": False,
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "Show staged diff for 'diff'",
                        "default": False,
                    },
                    "name": {
                        "type": "string",
                        "description": "Branch name for branch_create/delete/checkout",
                    },
                    "stash_message": {
                        "type": "string",
                        "description": "Optional stash message for stash_push",
                    },
                },
                "required": ["operation"],
            },
        }

    async def execute(
        self,
        arguments: GitToolInput,
        context: ToolExecutionContext,
    ) -> ToolResult:
        cwd = context.cwd
        cmd = _build_command(arguments, cwd)
        if cmd is None:
            return ToolResult(
                output=f"git: unsupported operation '{arguments.operation}'", is_error=True
            )
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(output="git command timed out", is_error=True)
        except FileNotFoundError:
            return ToolResult(output="git is not installed or not in PATH", is_error=True)

        combined = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            return ToolResult(output=combined or f"git exited with code {proc.returncode}", is_error=True)
        return ToolResult(output=combined or "(no output)")


def _build_command(args: GitToolInput, cwd: Path) -> list[str] | None:  # noqa: PLR0911
    op = args.operation

    if op == "status":
        cmd = ["git", "status", "--short"]
        if args.path:
            cmd.append("--")
            cmd.append(args.path)
        return cmd

    if op == "diff":
        cmd = ["git", "diff"]
        if args.staged:
            cmd.append("--cached")
        if args.ref and args.ref2:
            cmd.extend([f"{args.ref}..{args.ref2}"])
        elif args.ref:
            cmd.append(args.ref)
        if args.path:
            cmd.extend(["--", args.path])
        return cmd

    if op == "log":
        cmd = [
            "git",
            "log",
            f"--max-count={args.max_count}",
            "--oneline",
            "--decorate",
        ]
        if args.ref:
            cmd.append(args.ref)
        if args.path:
            cmd.extend(["--", args.path])
        return cmd

    if op == "show":
        cmd = ["git", "show"]
        if args.ref:
            if args.path:
                cmd.append(f"{args.ref}:{args.path}")
            else:
                cmd.append(args.ref)
        elif args.path:
            cmd.extend(["--", args.path])
        return cmd

    if op == "branch_list":
        return ["git", "branch", "--list", "--all", "--verbose"]

    if op == "branch_create":
        if not args.name:
            return None
        cmd = ["git", "branch", args.name]
        if args.ref:
            cmd.append(args.ref)
        return cmd

    if op == "branch_delete":
        if not args.name:
            return None
        return ["git", "branch", "--delete", args.name]

    if op == "stash_list":
        return ["git", "stash", "list"]

    if op == "stash_push":
        cmd = ["git", "stash", "push"]
        if args.stash_message:
            cmd.extend(["-m", args.stash_message])
        if args.path:
            cmd.extend(["--", args.path])
        return cmd

    if op == "stash_pop":
        return ["git", "stash", "pop"]

    if op == "commit":
        if not args.message:
            return None
        cmd = ["git", "commit", "-m", args.message]
        if args.all_files:
            cmd.append("-a")
        return cmd

    if op == "add":
        cmd = ["git", "add"]
        if args.all_files:
            cmd.append("--all")
        elif args.path:
            cmd.append(args.path)
        else:
            cmd.append("--all")
        return cmd

    if op == "checkout":
        if not args.name:
            return None
        return ["git", "checkout", args.name]

    return None
