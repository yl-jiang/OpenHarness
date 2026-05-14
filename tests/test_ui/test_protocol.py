"""Tests for React TUI protocol payloads."""

from __future__ import annotations

from openharness.state.app_state import AppState
from openharness.ui.protocol import BackendEvent


def _make_state() -> AppState:
    return AppState(
        model="claude-test",
        permission_mode="default",
        theme="default",
        cwd="/tmp/demo",
    )


def test_ready_event_includes_skill_aliases() -> None:
    event = BackendEvent.ready(
        _make_state(),
        tasks=[],
        commands=["/help", "/skills"],
        skills=["weekly-report", "write"],
    )

    assert event.skills == ["weekly-report", "write"]


def test_status_snapshot_includes_skill_aliases() -> None:
    event = BackendEvent.status_snapshot(
        state=_make_state(),
        mcp_servers=[],
        bridge_sessions=[],
        skills=["weekly-report", "write"],
    )

    assert event.skills == ["weekly-report", "write"]


def test_frontend_request_defaults_to_chat_input_mode() -> None:
    from openharness.ui.protocol import FrontendRequest

    request = FrontendRequest(type="submit_line", line="hello")
    assert request.input_mode == "chat"


def test_frontend_request_accepts_shell_input_mode() -> None:
    from openharness.ui.protocol import FrontendRequest

    request = FrontendRequest(type="submit_line", line="ls", input_mode="shell")
    assert request.input_mode == "shell"


def test_frontend_request_accepts_transcript_line_override() -> None:
    from openharness.ui.protocol import FrontendRequest

    request = FrontendRequest(
        type="submit_line",
        line="row-1\nrow-2",
        transcript_line="[Paste #1 - 2 lines]",
    )
    assert request.transcript_line == "[Paste #1 - 2 lines]"


def test_transcript_item_supports_user_shell_role() -> None:
    from openharness.ui.protocol import TranscriptItem

    item = TranscriptItem(role="user_shell", text="!ls")
    assert item.role == "user_shell"
