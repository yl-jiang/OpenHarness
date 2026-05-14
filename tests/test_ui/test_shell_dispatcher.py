"""Tests for the user-initiated shell command dispatcher."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.tools import create_default_tool_registry
from openharness.ui.shell_dispatcher import (
    SHELL_TOOL_ORIGIN,
    ShellCommandDispatcher,
    format_shell_history_message,
    parse_shell_command,
)


class _NeverCalledApiClient:
    """API client that explodes if anyone tries to call the model."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_message(self, request):  # pragma: no cover - guard
        self.calls += 1
        raise AssertionError("shell dispatcher must not invoke the model")
        if False:  # pragma: no cover
            yield None


class _OneShotApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[TextBlock(text="ok")],
            ),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


def _make_engine(
    tmp_path: Path,
    *,
    permission_mode: PermissionMode = PermissionMode.FULL_AUTO,
    api_client: Any | None = None,
) -> QueryEngine:
    return QueryEngine(
        api_client=api_client or _NeverCalledApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=permission_mode)),
        cwd=tmp_path,
        model="test-model",
        system_prompt="system",
    )


class _EventRecorder:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def __call__(self, event: Any) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# parse_shell_command
# ---------------------------------------------------------------------------


def test_parse_chat_mode_non_bang_returns_unhandled() -> None:
    assert parse_shell_command("hello world", input_mode="chat") == (False, None)


def test_parse_chat_mode_bang_command() -> None:
    assert parse_shell_command("!ls -la", input_mode="chat") == (True, "ls -la")


def test_parse_chat_mode_lone_bang_handled_but_no_command() -> None:
    assert parse_shell_command("!", input_mode="chat") == (True, None)
    assert parse_shell_command("  !  ", input_mode="chat") == (True, None)


def test_parse_shell_mode_treats_every_line_as_command() -> None:
    assert parse_shell_command("ls -la", input_mode="shell") == (True, "ls -la")
    assert parse_shell_command("   ", input_mode="shell") == (True, None)


# ---------------------------------------------------------------------------
# format_shell_history_message
# ---------------------------------------------------------------------------


def test_history_message_escapes_backticks_and_backslashes() -> None:
    rendered = format_shell_history_message("echo `bad`", "line `1`\npath\\to")
    assert "echo \\`bad\\`" in rendered
    assert "line \\`1\\`" in rendered
    assert "path\\\\to" in rendered
    assert rendered.count("```") == 4  # two opening + two closing fences


def test_history_message_truncates_long_output() -> None:
    huge = "x" * 50_000
    rendered = format_shell_history_message("echo", huge)
    assert "...[truncated]..." in rendered
    assert len(rendered) < 20_000


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_passes_through_for_non_shell_input(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    dispatcher = ShellCommandDispatcher()
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="how are you?",
        input_mode="chat",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is False
    assert outcome.executed is False
    assert recorder.events == []
    assert engine.messages == []


@pytest.mark.asyncio
async def test_dispatch_lone_bang_is_handled_but_does_not_execute(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    notifications: list[str] = []

    async def _print(text: str) -> None:
        notifications.append(text)

    dispatcher = ShellCommandDispatcher(print_system=_print)
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="!",
        input_mode="chat",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is True
    assert outcome.executed is False
    assert recorder.events == []
    assert engine.messages == []
    assert notifications and "!" in notifications[0]


@pytest.mark.asyncio
async def test_dispatch_runs_bang_command_and_injects_history(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    dispatcher = ShellCommandDispatcher()
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="!echo hello-shell-mode",
        input_mode="chat",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is True
    assert outcome.executed is True

    assert isinstance(recorder.events[0], ToolExecutionStarted)
    assert recorder.events[0].tool_name == "bash"
    assert recorder.events[0].tool_input == {
        "command": "echo hello-shell-mode",
        "origin": SHELL_TOOL_ORIGIN,
    }
    assert isinstance(recorder.events[-1], ToolExecutionCompleted)
    assert recorder.events[-1].is_error is False
    assert "hello-shell-mode" in recorder.events[-1].output
    assert recorder.events[-1].metadata is not None
    assert recorder.events[-1].metadata.get("origin") == SHELL_TOOL_ORIGIN

    assert len(engine.messages) == 1
    injected = engine.messages[0]
    assert injected.role == "user"
    assert "I ran the following shell command" in injected.text
    assert "echo hello-shell-mode" in injected.text
    assert "hello-shell-mode" in injected.text

    # Export checkpoint must mirror the injected history so session saves
    # do not lose the shell turn.
    assert engine.export_messages == engine.messages


@pytest.mark.asyncio
async def test_dispatch_shell_mode_runs_raw_line(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    dispatcher = ShellCommandDispatcher()
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="echo from-shell-mode",
        input_mode="shell",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is True
    assert outcome.executed is True
    started = recorder.events[0]
    assert isinstance(started, ToolExecutionStarted)
    assert started.tool_input["command"] == "echo from-shell-mode"


@pytest.mark.asyncio
async def test_dispatch_shell_mode_exit_keyword_signals_mode_change(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    dispatcher = ShellCommandDispatcher()
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="exit",
        input_mode="shell",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is True
    assert outcome.executed is False
    assert outcome.exit_shell_mode is True
    assert recorder.events == []
    assert engine.messages == []


@pytest.mark.asyncio
async def test_dispatch_permission_denied_does_not_execute(tmp_path: Path) -> None:
    # Plan mode denies mutating bash commands without confirmation prompt.
    engine = _make_engine(tmp_path, permission_mode=PermissionMode.PLAN)
    dispatcher = ShellCommandDispatcher()
    recorder = _EventRecorder()

    outcome = await dispatcher.dispatch(
        line="!touch should-not-exist.txt",
        input_mode="chat",
        engine=engine,
        tool_registry=engine._tool_registry,  # type: ignore[attr-defined]
        cwd=tmp_path,
        render_event=recorder,
    )

    assert outcome.handled is True
    assert outcome.executed is False
    assert isinstance(recorder.events[-1], ToolExecutionCompleted)
    assert recorder.events[-1].is_error is True
    assert "Permission denied" in recorder.events[-1].output

    # The command must not have run.
    assert not (tmp_path / "should-not-exist.txt").exists()

    # User still sees the denial in history so the next model turn is aware.
    assert len(engine.messages) == 1
    assert "Permission denied" in engine.messages[0].text


@pytest.mark.asyncio
async def test_inject_user_message_updates_export_checkpoint(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path)
    engine.inject_user_message("hello-from-shell")
    assert len(engine.messages) == 1
    assert engine.messages[0].role == "user"
    assert engine.messages[0].text == "hello-from-shell"
    # Export history must include the injected message even though no model
    # turn ran.
    assert engine.export_messages == engine.messages


@pytest.mark.asyncio
async def test_inject_user_message_preserves_prior_export_history(tmp_path: Path) -> None:
    engine = _make_engine(tmp_path, api_client=_OneShotApiClient())
    # Run one real turn so the engine has both _messages and _export_messages.
    async for _ in engine.submit_message("first prompt"):
        pass
    prior_export = list(engine.export_messages)
    assert prior_export, "expected a baseline export history"

    engine.inject_user_message("shell-output")
    # The new user message must be appended to the existing export history,
    # not silently dropped.
    assert engine.export_messages[: len(prior_export)] == prior_export
    assert engine.export_messages[-1].text == "shell-output"


@pytest.mark.asyncio
async def test_inject_user_message_consecutive_merged(tmp_path: Path) -> None:
    """Consecutive shell injections must be merged into one user message.

    Sending two consecutive user messages would violate the provider-required
    user/assistant alternation constraint.
    """
    engine = _make_engine(tmp_path)
    engine.inject_user_message("first-cmd-output")
    engine.inject_user_message("second-cmd-output")
    # Both outputs must live in a single user message.
    assert len(engine.messages) == 1
    assert engine.messages[0].role == "user"
    combined = engine.messages[0].text
    assert "first-cmd-output" in combined
    assert "second-cmd-output" in combined
