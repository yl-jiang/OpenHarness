"""User-initiated shell command dispatcher.

Handles direct ``!cmd`` input and shell-mode submissions: runs the command
through :class:`BashTool` under the standard :class:`ApprovalCoordinator`
permission flow, surfaces the execution as a ``tool``/``tool_result``
transcript pair, and injects the captured output into the engine's
conversation history as a ``user`` message **without** triggering a model
turn.

This module owns the small bit of glue that bridges user-initiated shell
execution and the engine's history.  It deliberately does **not** hold any
UI state, copy :class:`BashTool` logic, or persist runtime snapshots — the
caller (``runtime.handle_line``) saves snapshots once dispatch returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.tools.base import ToolExecutionContext, ToolRegistry
from openharness.tools.bash_tool import BashTool, BashToolInput
from openharness.utils.log import get_logger

logger = get_logger(__name__)

InputMode = Literal["chat", "shell"]
StreamRenderer = Callable[[StreamEvent], Awaitable[None]]
SystemPrinter = Callable[[str], Awaitable[None]]

MAX_HISTORY_OUTPUT_CHARS = 10_000
SHELL_TOOL_ORIGIN = "user_shell"

_BANG = "!"
_EXIT_KEYWORDS = frozenset({"exit", "quit"})


@dataclass(frozen=True)
class ShellDispatchOutcome:
    """Result of attempting to dispatch a line as a shell command."""

    handled: bool
    executed: bool = False
    exit_shell_mode: bool = False


def _escape_code_fence(text: str) -> str:
    """Escape characters that could close or escape the injected code fence.

    We escape backslashes first and then backticks so the output never
    prematurely terminates the ```` ``` ```` fence used by
    :func:`format_shell_history_message`.
    """
    return text.replace("\\", "\\\\").replace("`", "\\`")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[truncated]..."


def format_shell_history_message(command: str, output: str) -> str:
    """Render the user-history payload for a shell command + its output."""
    safe_command = _escape_code_fence(command)
    safe_output = _escape_code_fence(_truncate(output, MAX_HISTORY_OUTPUT_CHARS))
    return (
        "I ran the following shell command:\n"
        f"```sh\n{safe_command}\n```\n\n"
        "This produced the following result:\n"
        f"```\n{safe_output}\n```"
    )


def parse_shell_command(line: str, *, input_mode: InputMode) -> tuple[bool, str | None]:
    """Decide whether ``line`` should be dispatched as a shell command.

    Returns ``(is_shell, command)``:

    * ``(False, None)`` — not a shell input; caller should keep normal flow.
    * ``(True, None)`` — handled by dispatcher but nothing to execute (lone
      ``!`` in chat, empty line in shell mode, or exit keywords).
    * ``(True, command)`` — execute ``command``.
    """
    stripped = line.strip()
    if input_mode == "shell":
        if not stripped:
            return True, None
        return True, stripped
    if not stripped or not stripped.startswith(_BANG):
        return False, None
    command = stripped[1:].strip()
    if not command:
        return True, None
    return True, command


class ShellCommandDispatcher:
    """Run a ``!cmd``/shell-mode command and inject its output into history."""

    def __init__(self, *, print_system: SystemPrinter | None = None) -> None:
        self._print_system = print_system

    async def dispatch(
        self,
        *,
        line: str,
        input_mode: InputMode,
        engine: QueryEngine,
        tool_registry: ToolRegistry,
        cwd: str | Path,
        render_event: StreamRenderer,
    ) -> ShellDispatchOutcome:
        is_shell, command = parse_shell_command(line, input_mode=input_mode)
        if not is_shell:
            return ShellDispatchOutcome(handled=False)

        if command is None:
            if input_mode == "chat" and self._print_system is not None:
                await self._print_system(
                    "Shell prefix '!' detected without a command. "
                    "Type '!<command>' to run a local shell command."
                )
            return ShellDispatchOutcome(handled=True, executed=False)

        if input_mode == "shell" and command.lower() in _EXIT_KEYWORDS:
            return ShellDispatchOutcome(
                handled=True, executed=False, exit_shell_mode=True
            )

        bash_tool = tool_registry.get("bash")
        if not isinstance(bash_tool, BashTool):
            if self._print_system is not None:
                await self._print_system(
                    "Shell command unavailable: bash tool is not registered."
                )
            return ShellDispatchOutcome(handled=True, executed=False)

        tool_input_payload: dict[str, object] = {
            "command": command,
            "origin": SHELL_TOOL_ORIGIN,
        }
        await render_event(
            ToolExecutionStarted(tool_name="bash", tool_input=tool_input_payload)
        )

        bash_input = BashToolInput(command=command)
        exec_context = ToolExecutionContext(
            cwd=Path(cwd),
            metadata=dict(engine.tool_metadata),
            approval_coordinator=engine.approval_coordinator,
        )
        try:
            result = await bash_tool.execute(bash_input, exec_context)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(
                "shell_dispatcher_execute_failed",
                extra={"command": command},
            )
            error_text = f"Failed to spawn shell command: {exc}"
            await render_event(
                ToolExecutionCompleted(
                    tool_name="bash",
                    output=error_text,
                    is_error=True,
                    metadata={"origin": SHELL_TOOL_ORIGIN, "spawn_failed": True},
                )
            )
            engine.inject_user_message(
                format_shell_history_message(command, error_text)
            )
            return ShellDispatchOutcome(handled=True, executed=True)

        metadata = dict(result.metadata or {})
        metadata["origin"] = SHELL_TOOL_ORIGIN
        await render_event(
            ToolExecutionCompleted(
                tool_name="bash",
                output=result.output,
                is_error=result.is_error,
                metadata=metadata,
            )
        )
        engine.inject_user_message(
            format_shell_history_message(command, result.output)
        )
        return ShellDispatchOutcome(handled=True, executed=True)


__all__ = [
    "InputMode",
    "MAX_HISTORY_OUTPUT_CHARS",
    "SHELL_TOOL_ORIGIN",
    "ShellCommandDispatcher",
    "ShellDispatchOutcome",
    "format_shell_history_message",
    "parse_shell_command",
]
