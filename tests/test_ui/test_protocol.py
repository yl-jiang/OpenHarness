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
