"""Structured protocol models for the React TUI backend."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from openharness.state.app_state import AppState
from openharness.bridge.manager import BridgeSessionRecord
from openharness.mcp.types import McpConnectionStatus
from openharness.tasks.types import TaskRecord


class FrontendRequest(BaseModel):
    """One request sent from the React frontend to the Python backend."""

    type: Literal[
        "submit_line",
        "cancel_line",
        "permission_response",
        "question_response",
        "list_sessions",
        "select_command",
        "apply_select_command",
        "shutdown",
    ]
    line: str | None = None
    transcript_line: str | None = None
    command: str | None = None
    value: str | None = None
    request_id: str | None = None
    allowed: bool | None = None
    permission_reply: Literal["once", "always", "reject"] | None = None
    answer: str | None = None
    input_mode: Literal["chat", "shell"] = "chat"


class TranscriptItem(BaseModel):
    """One transcript row rendered by the frontend."""

    role: Literal[
        "system", "user", "user_shell", "assistant", "tool", "tool_result", "log"
    ]
    text: str
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    is_error: bool | None = None


class TaskSnapshot(BaseModel):
    """UI-safe task representation."""

    id: str
    type: str
    status: str
    description: str
    started_at: float | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_record(cls, record: TaskRecord) -> "TaskSnapshot":
        return cls(
            id=record.id,
            type=record.type,
            status=record.status,
            description=record.description,
            started_at=record.started_at,
            metadata=dict(record.metadata),
        )


class BackendEvent(BaseModel):
    """One event sent from the Python backend to the React frontend."""

    type: Literal[
        "ready",
        "state_snapshot",
        "tasks_snapshot",
        "transcript_item",
        "compact_progress",
        "assistant_delta",
        "assistant_complete",
        "line_complete",
        "tool_started",
        "tool_completed",
        "clear_transcript",
        "modal_request",
        "select_request",
        "todo_update",
        "plan_mode_change",
        "swarm_status",
        "error",
        "shutdown",
    ]
    select_options: list[dict[str, Any]] | None = None
    message: str | None = None
    item: TranscriptItem | None = None
    state: dict[str, Any] | None = None
    tasks: list[TaskSnapshot] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    bridge_sessions: list[dict[str, Any]] | None = None
    commands: list[str] | None = None
    skills: list[str] | None = None
    modal: dict[str, Any] | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    output: str | None = None
    is_error: bool | None = None
    compact_phase: str | None = None
    compact_trigger: str | None = None
    attempt: int | None = None
    compact_checkpoint: str | None = None
    compact_metadata: dict[str, Any] | None = None
    # New fields for enhanced events
    todo_markdown: str | None = None
    plan_mode: str | None = None
    swarm_teammates: list[dict[str, Any]] | None = None
    swarm_notifications: list[dict[str, Any]] | None = None
    # Terminal-state reason for line_complete events
    reason: str | None = None

    @classmethod
    def ready(
        cls,
        state: AppState,
        tasks: list[TaskRecord],
        commands: list[str],
        skills: list[str],
    ) -> "BackendEvent":
        return cls(
            type="ready",
            state=_state_payload(state),
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
            mcp_servers=[],
            bridge_sessions=[],
            commands=commands,
            skills=skills,
        )

    @classmethod
    def line_complete(cls, *, reason: str = "completed", detail: str | None = None) -> "BackendEvent":
        return cls(type="line_complete", reason=reason, message=detail)

    @classmethod
    def state_snapshot(cls, state: AppState) -> "BackendEvent":
        return cls(type="state_snapshot", state=_state_payload(state))

    @classmethod
    def tasks_snapshot(cls, tasks: list[TaskRecord]) -> "BackendEvent":
        return cls(
            type="tasks_snapshot",
            tasks=[TaskSnapshot.from_record(task) for task in tasks],
        )

    @classmethod
    def status_snapshot(
        cls,
        *,
        state: AppState,
        mcp_servers: list[McpConnectionStatus],
        bridge_sessions: list[BridgeSessionRecord],
        skills: list[str],
    ) -> "BackendEvent":
        return cls(
            type="state_snapshot",
            state=_state_payload(state),
            mcp_servers=[
                {
                    "name": server.name,
                    "state": server.state,
                    "detail": server.detail,
                    "transport": server.transport,
                    "auth_configured": server.auth_configured,
                    "tool_count": len(server.tools),
                    "resource_count": len(server.resources),
                }
                for server in mcp_servers
            ],
            bridge_sessions=[
                {
                    "session_id": session.session_id,
                    "command": session.command,
                    "cwd": session.cwd,
                    "pid": session.pid,
                    "status": session.status,
                    "started_at": session.started_at,
                    "output_path": session.output_path,
                }
                for session in bridge_sessions
            ],
            skills=skills,
        )


def _state_payload(state: AppState) -> dict[str, Any]:
    return {
        "model": state.model,
        "cwd": state.cwd,
        "git_branch": state.git_branch,
        "provider": state.provider,
        "auth_status": state.auth_status,
        "base_url": state.base_url,
        "permission_mode": _format_permission_mode(state.permission_mode),
        "theme": state.theme,
        "vim_enabled": state.vim_enabled,
        "voice_enabled": state.voice_enabled,
        "voice_available": state.voice_available,
        "voice_reason": state.voice_reason,
        "fast_mode": state.fast_mode,
        "effort": state.effort,
        "passes": state.passes,
        "mcp_connected": state.mcp_connected,
        "mcp_failed": state.mcp_failed,
        "bridge_sessions": state.bridge_sessions,
        "output_style": state.output_style,
        "keybindings": dict(state.keybindings),
        "input_tokens": state.input_tokens,
        "output_tokens": state.output_tokens,
        "reviews_completed": state.reviews_completed,
    }


_MODE_LABELS = {
    "default": "Default",
    "plan": "Plan Mode",
    "full_auto": "Auto",
    "PermissionMode.DEFAULT": "Default",
    "PermissionMode.PLAN": "Plan Mode",
    "PermissionMode.FULL_AUTO": "Auto",
}


def _format_permission_mode(raw: str) -> str:
    """Convert raw permission mode to human-readable label."""
    return _MODE_LABELS.get(raw, raw)


__all__ = [
    "BackendEvent",
    "FrontendRequest",
    "TaskSnapshot",
    "TranscriptItem",
]
