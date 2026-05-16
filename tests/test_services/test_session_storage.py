"""Tests for session persistence."""

from __future__ import annotations

import json
from pathlib import Path

from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.services.session_storage import (
    export_session_markdown,
    get_project_session_dir,
    load_session_snapshot,
    save_session_snapshot,
)


def test_save_and_load_session_snapshot(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = save_session_snapshot(
        cwd=project,
        model="claude-test",
        system_prompt="system",
        messages=[ConversationMessage(role="user", content=[TextBlock(text="hello")])],
        usage=UsageSnapshot(input_tokens=1, output_tokens=2),
        tool_metadata={
            "task_focus_state": {"goal": "Fix compact carry-over"},
            "recent_verified_work": ["Focused session storage test passed"],
        },
    )

    assert path.exists()
    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["model"] == "claude-test"
    assert snapshot["usage"]["output_tokens"] == 2
    assert snapshot["tool_metadata"]["task_focus_state"]["goal"] == "Fix compact carry-over"
    assert snapshot["tool_metadata"]["recent_verified_work"] == ["Focused session storage test passed"]


def test_export_session_markdown(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    path = export_session_markdown(
        cwd=project,
        usage=UsageSnapshot(input_tokens=1000, output_tokens=234),
        session_id="test-session-123",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="hello")]),
            ConversationMessage(
                role="assistant",
                content=[
                    TextBlock(text="world"),
                    ToolUseBlock(id="toolu_123", name="read_file", input={"path": "a.py"}),
                ],
            ),
            ConversationMessage(
                role="user",
                content=[ToolResultBlock(tool_use_id="toolu_123", content="file content")],
            ),
        ],
    )

    assert path.exists()
    assert path.parent == project.resolve()
    assert path.name.startswith("openharness-export-test-ses-")
    content = path.read_text(encoding="utf-8")
    assert "session_id: test-session-123" in content
    assert "work_dir: " + str(project.resolve()) in content
    assert "message_count: 3" in content
    assert "token_count: 1234" in content
    assert "# OpenHarness Session Export" in content
    assert "- **Topic**: hello" in content
    assert "- **Conversation**: 1 turns | 1 tool calls | 1,234 tokens" in content
    assert "## Turn 1" in content
    assert "hello" in content
    assert "world" in content
    assert "#### Tool Call: read_file" in content
    assert "<!-- call_id: toolu_123 -->" in content
    assert '"path": "a.py"' in content
    assert "<details><summary>Tool Result: read_file</summary>" in content
    assert "file content" in content


def test_load_session_snapshot_sanitizes_legacy_empty_assistant_messages(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    project = tmp_path / "repo"
    project.mkdir()

    target_dir = get_project_session_dir(project)
    payload = {
        "session_id": "legacy123",
        "cwd": str(project),
        "model": "claude-test",
        "system_prompt": "system",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "assistant", "content": None},
            {"role": "assistant", "content": []},
            {"role": "assistant", "content": [{"type": "text", "text": "world"}]},
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "tool_metadata": {},
        "created_at": 1.0,
        "summary": "hello",
        "message_count": 4,
    }
    (target_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")

    snapshot = load_session_snapshot(project)
    assert snapshot is not None
    assert snapshot["message_count"] == 2
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"][0]["text"] == "world"
