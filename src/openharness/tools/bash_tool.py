"""Shell command execution tool."""

from __future__ import annotations

import asyncio
import os
import shlex
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from openharness.sandbox import SandboxUnavailableError
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolResult
from openharness.utils.shell import create_shell_subprocess


_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS = 2.0
_NON_INTERACTIVE_ENV_OVERRIDES = {
    "CI": "1",
    "GIT_PAGER": "cat",
    "PAGER": "cat",
    "MANPAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
}
_COMMAND_SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
_INTERACTIVE_PROGRAMS = frozenset({"less", "more", "most", "vim", "vi", "nvim", "view", "top", "htop", "watch", "man"})
_GIT_PAGER_SUBCOMMANDS = frozenset({"diff", "log", "show"})
_GIT_PAGER_DISABLE_MARKERS = frozenset({"--no-pager", "git_pager=cat", "pager=cat", "manpager=cat"})


class BashToolInput(BaseModel):
    """Arguments for the bash tool."""

    command: str = Field(description="Shell command to execute")
    cwd: str | None = Field(default=None, description="Working directory override")
    timeout_seconds: int = Field(default=600, ge=1, le=600)


class BashTool(BaseTool):
    """Execute a shell command with stdout/stderr capture."""

    name = "bash"
    description = (
        "Run a non-interactive shell command in the local repository. "
        "Commands run without a TTY, stdout and stderr are merged and returned. "
        "Prefer non-interactive flags (e.g. -y, --no-pager) when available."
    )
    input_model = BashToolInput

    def to_api_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Working directory override",
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Timeout in seconds (1-600)",
                        "default": 600,
                    },
                },
                "required": ["command"],
            },
        }

    async def execute(self, arguments: BashToolInput, context: ToolExecutionContext) -> ToolResult:
        cwd = Path(arguments.cwd).expanduser() if arguments.cwd else context.cwd
        preflight_error = _preflight_interactive_command(arguments.command)
        if preflight_error is not None:
            return ToolResult(
                output=preflight_error,
                is_error=True,
                metadata={"interactive_required": True},
            )
        process: asyncio.subprocess.Process | None = None
        try:
            process = await create_shell_subprocess(
                arguments.command,
                cwd=cwd,
                prefer_pty=False,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=_build_non_interactive_env(),
            )
        except SandboxUnavailableError as exc:
            return ToolResult(output=str(exc), is_error=True)
        except asyncio.CancelledError:
            if process is not None:
                await _terminate_process(process, force=False)
            raise

        output_buffer = bytearray()

        async def _read_all() -> None:
            assert process.stdout is not None
            try:
                while True:
                    chunk = await process.stdout.read(65536)
                    if not chunk:
                        break
                    output_buffer.extend(chunk)
            except OSError:
                pass

        try:
            await asyncio.wait_for(
                asyncio.gather(process.wait(), _read_all()),
                timeout=arguments.timeout_seconds,
            )
        except asyncio.TimeoutError:
            await _terminate_process(process, force=True)
            output_buffer.extend(await _read_remaining_output(process))
            return ToolResult(
                output=_format_timeout_output(
                    output_buffer,
                    command=arguments.command,
                    timeout_seconds=arguments.timeout_seconds,
                ),
                is_error=True,
                metadata={"returncode": process.returncode, "timed_out": True},
            )
        except asyncio.CancelledError:
            await _terminate_process(process, force=False)
            raise

        text = _format_output(output_buffer)
        return ToolResult(
            output=text,
            is_error=process.returncode != 0,
            metadata={"returncode": process.returncode},
        )


async def _terminate_process(process: asyncio.subprocess.Process, *, force: bool) -> None:
    if process.returncode is not None:
        return
    if force:
        process.kill()
        await process.wait()
        return
    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def _read_remaining_output(process: asyncio.subprocess.Process) -> bytearray:
    output_buffer = bytearray()
    if process.stdout is not None:
        try:
            remaining = await asyncio.wait_for(
                process.stdout.read(),
                timeout=_READ_REMAINING_OUTPUT_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            remaining = b""
        output_buffer.extend(remaining)
    return output_buffer


def _format_output(output_buffer: bytearray) -> str:
    text = output_buffer.decode("utf-8", errors="replace").replace("\r\n", "\n").strip()
    if not text:
        return "(no output)"
    if len(text) > 12000:
        return f"{text[:12000]}\n...[truncated]..."
    return text


def _format_timeout_output(output_buffer: bytearray, *, command: str, timeout_seconds: int) -> str:
    parts = [f"Command timed out after {timeout_seconds} seconds."]
    text = _format_output(output_buffer)
    if text != "(no output)":
        parts.extend(["", "Partial output:", text])
    hint = _interactive_command_hint(command=command, output=text)
    if hint:
        parts.extend(["", hint])
    return "\n".join(parts)


def _preflight_interactive_command(command: str) -> str | None:
    lowered_command = command.lower()
    if _looks_like_interactive_scaffold(lowered_command):
        return (
            "This command appears to require interactive input before it can continue. "
            "The bash tool is non-interactive, so it cannot answer installer/scaffold prompts live. "
            "Prefer non-interactive flags (for example --yes, -y, --skip-install, --defaults, --non-interactive), "
            "or run the scaffolding step once in an external terminal before asking the agent to continue."
        )
    if _looks_like_explicit_interactive_command(command):
        return (
            "This command appears to require interactive input before it can continue. "
            "The bash tool runs without a TTY, so it cannot drive pagers, editors, or other interactive terminal programs live. "
            "Prefer a non-interactive alternative or run this command in an external terminal."
        )
    if _looks_like_git_pager_command(command):
        return (
            "This git command may open a pager in interactive terminals. "
            "The bash tool is non-interactive, so rerun it with --no-pager "
            "(for example `git --no-pager diff ...`) or use another non-interactive form."
        )
    return None


def _interactive_command_hint(*, command: str, output: str) -> str | None:
    lowered_command = command.lower()
    if (
        _looks_like_interactive_scaffold(lowered_command)
        or _looks_like_explicit_interactive_command(command)
        or _looks_like_prompt(output)
    ):
        return (
            "This command appears to require interactive input. "
            "The bash tool is non-interactive, so prefer non-interactive flags "
            "(for example --yes, -y, --skip-install, or similar) or run the "
            "scaffolding step once in an external terminal before continuing."
        )
    if _looks_like_git_pager_command(command):
        return (
            "This git command may require a pager in interactive terminals. "
            "Rerun it with --no-pager when using the non-interactive bash tool."
        )
    return None


def _looks_like_interactive_scaffold(lowered_command: str) -> bool:
    scaffold_markers: tuple[str, ...] = (
        "create-next-app",
        "npm create ",
        "pnpm create ",
        "yarn create ",
        "bun create ",
        "pnpm dlx ",
        "npm init ",
        "pnpm init ",
        "yarn init ",
        "bunx create-",
        "npx create-",
    )
    non_interactive_markers: tuple[str, ...] = (
        "--yes",
        " -y",
        "--skip-install",
        "--defaults",
        "--non-interactive",
        "--ci",
    )
    return any(marker in lowered_command for marker in scaffold_markers) and not any(
        marker in lowered_command for marker in non_interactive_markers
    )


def _looks_like_prompt(output: str) -> bool:
    if not output:
        return False
    prompt_markers: Iterable[str] = (
        "would you like",
        "ok to proceed",
        "select an option",
        "which",
        "press enter to continue",
        "?",
    )
    lowered_output = output.lower()
    return any(marker in lowered_output for marker in prompt_markers)


def _build_non_interactive_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(_NON_INTERACTIVE_ENV_OVERRIDES)
    return env


def _tokenize_shell_command(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return command.split()


def _split_command_segments(tokens: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _COMMAND_SEPARATORS:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _strip_env_prefix(tokens: list[str]) -> list[str]:
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token == "env":
            index += 1
            continue
        if "=" in token and not token.startswith(("=", "./", "../", "/")):
            name, _, _value = token.partition("=")
            if name:
                index += 1
                continue
        break
    return tokens[index:]


def _looks_like_explicit_interactive_command(command: str) -> bool:
    for segment in _split_command_segments(_tokenize_shell_command(command)):
        stripped = _strip_env_prefix(segment)
        if not stripped:
            continue
        program = stripped[0].lower()
        if program in _INTERACTIVE_PROGRAMS:
            return True
        if program == "tail" and any(flag in {"-f", "-F", "--follow"} for flag in stripped[1:]):
            return True
    return False


def _looks_like_git_pager_command(command: str) -> bool:
    for segment in _split_command_segments(_tokenize_shell_command(command)):
        lowered_segment = [token.lower() for token in _strip_env_prefix(segment)]
        if not lowered_segment or lowered_segment[0] != "git":
            continue
        if any(marker in lowered_segment for marker in _GIT_PAGER_DISABLE_MARKERS):
            continue
        for token in lowered_segment[1:]:
            if token.startswith("-"):
                continue
            return token in _GIT_PAGER_SUBCOMMANDS
    return False
