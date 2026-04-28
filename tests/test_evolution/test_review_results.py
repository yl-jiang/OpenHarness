"""Tests for self-evolution review result extraction and callback."""

from __future__ import annotations

from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.evolution.self_evolution import (
    ReviewAction,
    extract_review_actions,
    format_review_summary,
)


# ---------------------------------------------------------------------------
# P2a: extract_review_actions
# ---------------------------------------------------------------------------

def test_extract_memory_add_action():
    """Should detect a successful memory add from tool use + result pair."""
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="memory",
                    input={"action": "add", "target": "user", "content": "Prefers concise replies."},
                    id="tool-1",
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="tool-1",
                    content='{"success": true, "message": "Entry added."}',
                ),
            ],
        ),
    ]

    actions = extract_review_actions(messages)

    assert len(actions) == 1
    assert actions[0].tool == "memory"
    assert actions[0].action == "add"
    assert actions[0].target == "user"
    assert actions[0].success is True


def test_extract_skill_create_action():
    """Should detect a successful skill creation."""
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="skill_manager",
                    input={"action": "write", "name": "pytest-runner", "content": "..."},
                    id="tool-2",
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="tool-2",
                    content="Skill 'pytest-runner' created successfully.",
                ),
            ],
        ),
    ]

    actions = extract_review_actions(messages)

    assert len(actions) == 1
    assert actions[0].tool == "skill_manager"
    assert actions[0].action == "write"
    assert actions[0].detail == "pytest-runner"
    assert actions[0].success is True


def test_extract_ignores_failed_actions():
    """Should not include actions where the tool result indicates failure."""
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="memory",
                    input={"action": "add", "target": "memory", "content": "..."},
                    id="tool-3",
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(
                    tool_use_id="tool-3",
                    content='{"success": false, "error": "exceeds limit"}',
                    is_error=True,
                ),
            ],
        ),
    ]

    actions = extract_review_actions(messages)
    assert len(actions) == 0


def test_extract_ignores_read_actions():
    """Read operations should not be surfaced as review actions."""
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="memory",
                    input={"action": "read", "target": "memory"},
                    id="tool-4",
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="tool-4", content='{"success": true}'),
            ],
        ),
    ]

    actions = extract_review_actions(messages)
    assert len(actions) == 0


def test_extract_no_actions_from_text_only_conversation():
    """Pure text conversation should yield no actions."""
    messages = [
        ConversationMessage(role="assistant", content=[TextBlock(text="Nothing to save.")]),
    ]

    actions = extract_review_actions(messages)
    assert len(actions) == 0


def test_extract_multiple_actions():
    """Should handle mixed memory + skill actions in one review."""
    messages = [
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="memory",
                    input={"action": "add", "target": "user", "content": "Uses macOS."},
                    id="t1",
                ),
                ToolUseBlock(
                    name="skill_manager",
                    input={"action": "write", "name": "docker-debug", "content": "..."},
                    id="t2",
                ),
            ],
        ),
        ConversationMessage(
            role="user",
            content=[
                ToolResultBlock(tool_use_id="t1", content='{"success": true, "message": "Entry added."}'),
                ToolResultBlock(tool_use_id="t2", content="Skill 'docker-debug' created successfully."),
            ],
        ),
    ]

    actions = extract_review_actions(messages)
    assert len(actions) == 2


# ---------------------------------------------------------------------------
# P2b: format_review_summary
# ---------------------------------------------------------------------------

def test_format_review_summary_memory_and_skill():
    """Summary should be compact and mention both memory and skill actions."""
    actions = [
        ReviewAction(tool="memory", action="add", target="user", success=True),
        ReviewAction(tool="skill_manager", action="write", detail="pytest-runner", success=True),
    ]

    summary = format_review_summary(actions)

    assert "💾" in summary
    assert "user" in summary.lower() or "memory" in summary.lower()
    assert "pytest-runner" in summary


def test_format_review_summary_empty():
    """No actions should produce empty string."""
    assert format_review_summary([]) == ""


def test_format_review_summary_memory_only():
    """Memory-only summary should mention the target."""
    actions = [
        ReviewAction(tool="memory", action="add", target="memory", success=True),
    ]
    summary = format_review_summary(actions)
    assert "💾" in summary
    assert "memory" in summary.lower()
