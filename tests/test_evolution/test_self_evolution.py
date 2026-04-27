"""Tests for self-evolution review triggers."""

from __future__ import annotations

from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.evolution.self_evolution import (
    SelfEvolutionConfig,
    SelfEvolutionController,
    SelfEvolutionReviewRequest,
    build_self_evolution_review_prompt,
)


class RecordingRunner:
    def __init__(self) -> None:
        self.requests: list[SelfEvolutionReviewRequest] = []

    def spawn_review(self, request: SelfEvolutionReviewRequest) -> None:
        self.requests.append(request)


def test_builds_hermes_style_review_prompts():
    combined = build_self_evolution_review_prompt(review_memory=True, review_skills=True)
    skills_only = build_self_evolution_review_prompt(review_memory=False, review_skills=True)

    assert "Memory" in combined
    assert "Skills" in combined
    assert "non-trivial approach" in combined
    assert "create a new one" in combined
    assert "saving or updating a skill" in skills_only


def test_controller_triggers_skill_review_after_tool_iterations():
    runner = RecordingRunner()
    controller = SelfEvolutionController(
        SelfEvolutionConfig(enabled=True, skill_review_interval=2, memory_review_interval=0),
        runner,
    )
    metadata: dict[str, object] = {}
    snapshot = [ConversationMessage.from_user_text("fix the bug")]

    controller.begin_user_turn(metadata, memory_tool_available=True, skill_tool_available=True)
    controller.observe_assistant_turn(
        metadata,
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(name="read_file", input={"path": "a.py"})],
        ),
    )
    controller.maybe_spawn_review(metadata, snapshot, latest_user_prompt="fix the bug")
    assert runner.requests == []

    controller.observe_assistant_turn(
        metadata,
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(name="bash", input={"command": "pytest"})],
        ),
    )
    controller.maybe_spawn_review(metadata, snapshot, latest_user_prompt="fix the bug")

    assert len(runner.requests) == 1
    assert runner.requests[0].review_skills is True
    assert runner.requests[0].review_memory is False
    assert runner.requests[0].latest_user_prompt == "fix the bug"


def test_controller_resets_skill_counter_after_skill_write():
    runner = RecordingRunner()
    controller = SelfEvolutionController(
        SelfEvolutionConfig(enabled=True, skill_review_interval=1, memory_review_interval=0),
        runner,
    )
    metadata: dict[str, object] = {}

    controller.begin_user_turn(metadata, memory_tool_available=False, skill_tool_available=True)
    controller.observe_assistant_turn(
        metadata,
        ConversationMessage(
            role="assistant",
            content=[
                ToolUseBlock(
                    name="skill_manager",
                    input={"action": "patch", "name": "pytest"},
                )
            ],
        ),
    )
    controller.maybe_spawn_review(metadata, [ConversationMessage.from_user_text("done")])

    assert runner.requests == []


def test_controller_triggers_memory_review_by_user_turn_interval():
    runner = RecordingRunner()
    controller = SelfEvolutionController(
        SelfEvolutionConfig(enabled=True, skill_review_interval=0, memory_review_interval=2),
        runner,
    )
    metadata: dict[str, object] = {}
    snapshot = [ConversationMessage(role="assistant", content=[TextBlock(text="ok")])]

    controller.begin_user_turn(metadata, memory_tool_available=True, skill_tool_available=False)
    controller.maybe_spawn_review(metadata, snapshot)
    assert runner.requests == []

    controller.begin_user_turn(metadata, memory_tool_available=True, skill_tool_available=False)
    controller.maybe_spawn_review(metadata, snapshot)

    assert len(runner.requests) == 1
    assert runner.requests[0].review_memory is True
    assert runner.requests[0].review_skills is False
