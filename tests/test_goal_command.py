"""Tests for the /goal command parser and formatter."""

from __future__ import annotations

import pytest

from openharness.commands.core import CommandContext
from openharness.commands.goal import format_goal_status, parse_goal_command
from openharness.commands.registry import create_default_command_registry
from openharness.goal.state import GOAL_MODE_KEY, GoalMode


def test_parse_status_default() -> None:
    assert parse_goal_command("") == {"kind": "status"}
    assert parse_goal_command("status") == {"kind": "status"}
    assert parse_goal_command("  ") == {"kind": "status"}


def test_parse_control_subcommands() -> None:
    assert parse_goal_command("pause")["kind"] == "pause"
    assert parse_goal_command("resume")["kind"] == "resume"
    assert parse_goal_command("cancel")["kind"] == "cancel"


def test_parse_control_word_as_objective() -> None:
    # "pause the rollout" — multi-word: NOT the "pause" subcommand.
    parsed = parse_goal_command("pause the rollout")
    assert parsed["kind"] == "create"
    assert parsed["objective"] == "pause the rollout"


def test_parse_separator_preserves_reserved_word() -> None:
    parsed = parse_goal_command("-- pause the rollout")
    assert parsed["kind"] == "create"
    assert parsed["objective"] == "pause the rollout"


def test_parse_replace() -> None:
    parsed = parse_goal_command("replace Ship feature X")
    assert parsed["kind"] == "create"
    assert parsed["replace"] is True
    assert parsed["objective"] == "Ship feature X"


def test_parse_create_simple() -> None:
    parsed = parse_goal_command("Ship feature X")
    assert parsed["kind"] == "create"
    assert parsed["replace"] is False
    assert parsed["objective"] == "Ship feature X"


def test_parse_queue_list() -> None:
    assert parse_goal_command("queue")["kind"] == "queue_list"
    assert parse_goal_command("queue ")["kind"] == "queue_list"


def test_parse_queue_add() -> None:
    parsed = parse_goal_command("queue add Ship feature Y")
    assert parsed["kind"] == "queue_add"
    assert parsed["objective"] == "Ship feature Y"
    assert parsed.get("priority", 0) == 0


def test_parse_queue_add_with_priority() -> None:
    parsed = parse_goal_command("queue add --priority 5 Do tests")
    assert parsed["kind"] == "queue_add"
    assert parsed["objective"] == "Do tests"
    assert parsed["priority"] == 5


def test_parse_queue_add_with_separator() -> None:
    parsed = parse_goal_command("queue add -- pause everything")
    assert parsed["kind"] == "queue_add"
    assert parsed["objective"] == "pause everything"


def test_parse_queue_add_invalid_priority() -> None:
    parsed = parse_goal_command("queue add --priority abc Do thing")
    assert parsed["kind"] == "error"


def test_parse_queue_remove() -> None:
    parsed = parse_goal_command("queue remove abc123")
    assert parsed["kind"] == "queue_remove"
    assert parsed["queue_id"] == "abc123"


def test_parse_queue_clear() -> None:
    assert parse_goal_command("queue clear")["kind"] == "queue_clear"


def test_parse_queue_unknown_errors() -> None:
    parsed = parse_goal_command("queue frobnicate")
    assert parsed["kind"] == "error"
    assert "Usage" in parsed["message"]


def test_parse_next_and_skip() -> None:
    assert parse_goal_command("next")["kind"] == "next"
    assert parse_goal_command("skip")["kind"] == "skip"


def test_parse_empty_after_replace_is_error() -> None:
    parsed = parse_goal_command("replace")
    assert parsed["kind"] == "error"


def test_parse_objective_too_long() -> None:
    parsed = parse_goal_command("x" * 5000)
    assert parsed["kind"] == "error"
    assert "too long" in parsed["message"].lower()


def test_format_goal_status_active() -> None:
    gm = GoalMode({})
    snap = gm.create_goal("Ship feature X", completion_criterion="tests pass")
    text = format_goal_status(snap)
    assert "Ship feature X" in text
    assert "tests pass" in text
    assert "active" in text
    assert "Budget:" in text


def test_format_goal_status_with_budget() -> None:
    from openharness.goal.state import GoalBudgetLimits

    gm = GoalMode({})
    gm.create_goal("Ship feature X")
    gm.set_budget_limits(GoalBudgetLimits(turn_budget=10))
    text = format_goal_status(gm.get_goal())
    assert "turns" in text
    assert "10" in text


# ---------------------------------------------------------------- /permissions restore integration


class _StubEngine:
    def __init__(self, tool_metadata: dict) -> None:
        self.tool_metadata = tool_metadata
        self.permission_checker = None
        self.require_explicit_done = False
        self._model = "test"
        self._settings = None
        self.injected_messages: list[str] = []

    def set_permission_checker(self, checker):
        self.permission_checker = checker

    def set_require_explicit_done(self, value: bool):
        self.require_explicit_done = value

    def inject_user_message(self, text: str) -> None:
        self.injected_messages.append(text)


@pytest.mark.asyncio
async def test_permissions_restore_no_goal_change() -> None:
    """No prior goal → /permissions restore errors with a friendly message."""
    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    registry = create_default_command_registry()
    command, args = registry.lookup("/permissions restore")
    context = CommandContext(engine=_StubEngine(metadata))
    result = await command.handler(args, context)
    assert result.message is not None
    assert "No goal-driven permission change" in result.message


@pytest.mark.asyncio
async def test_permissions_restore_with_recorded_mode() -> None:
    """A goal with original_permission_mode set → /permissions restore switches."""
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing", original_permission_mode="plan")
    metadata[GOAL_MODE_KEY] = gm
    registry = create_default_command_registry()
    command, args = registry.lookup("/permissions restore")
    context = CommandContext(engine=_StubEngine(metadata))
    result = await command.handler(args, context)
    assert result.message is not None
    assert "restored" in result.message.lower() or "plan" in result.message.lower()
    assert result.refresh_runtime is True


# ---------------------------------------------------------------- /goal queue


@pytest.mark.asyncio
async def test_goal_queue_list_empty() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    metadata[GOAL_QUEUE_KEY] = GoalQueueStore(metadata)
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal queue")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "empty" in result.message.lower()


@pytest.mark.asyncio
async def test_goal_queue_add() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    metadata[GOAL_QUEUE_KEY] = GoalQueueStore(metadata)
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal queue add Ship feature Y")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "Queued goal" in result.message
    assert len(metadata[GOAL_QUEUE_KEY]) == 1


@pytest.mark.asyncio
async def test_goal_queue_add_with_priority() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    metadata[GOAL_QUEUE_KEY] = GoalQueueStore(metadata)
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal queue add --priority 5 Do tests")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "Queued goal" in result.message
    assert metadata[GOAL_QUEUE_KEY].peek().priority == 5


@pytest.mark.asyncio
async def test_goal_queue_clear() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    queue = GoalQueueStore(metadata)
    queue.enqueue("A")
    queue.enqueue("B")
    metadata[GOAL_QUEUE_KEY] = queue
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal queue clear")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "cleared" in result.message.lower()
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_goal_next_promotes_queue_head() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    gm = GoalMode(metadata)
    metadata[GOAL_MODE_KEY] = gm
    queue = GoalQueueStore(metadata)
    queue.enqueue("Goal B")
    metadata[GOAL_QUEUE_KEY] = queue
    # No active goal: /goal next should promote the queue head.
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal next")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "Started next queued goal" in result.message
    assert result.submit_prompt == "Goal B"
    # Goal B is now active.
    assert gm.get_goal() is not None
    assert gm.get_goal().objective == "Goal B"
    assert len(queue) == 0


@pytest.mark.asyncio
async def test_goal_skip_pops_without_starting() -> None:
    from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore

    metadata: dict = {}
    metadata[GOAL_MODE_KEY] = GoalMode(metadata)
    queue = GoalQueueStore(metadata)
    queue.enqueue("Goal B")
    metadata[GOAL_QUEUE_KEY] = queue
    registry = create_default_command_registry()
    command, args = registry.lookup("/goal skip")
    result = await command.handler(args, CommandContext(engine=_StubEngine(metadata)))
    assert result.message is not None
    assert "Skipped queued goal" in result.message
    assert result.submit_prompt is None
    # Goal B was popped but NOT started.
    assert len(queue) == 0
    gm = metadata[GOAL_MODE_KEY]
    assert gm.get_goal() is None
