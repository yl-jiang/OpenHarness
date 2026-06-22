"""Tests for goal/queue.py — GoalQueueStore."""

from __future__ import annotations

import pytest

from openharness.goal.queue import GOAL_QUEUE_KEY, GoalQueueStore
from openharness.goal.state import GoalBudgetLimits, GoalMode


@pytest.fixture
def metadata() -> dict:
    return {}


@pytest.fixture
def store(metadata: dict) -> GoalQueueStore:
    return GoalQueueStore(metadata)


def test_enqueue_and_list(store: GoalQueueStore) -> None:
    q1 = store.enqueue("A")
    q2 = store.enqueue("B")
    assert len(store) == 2
    items = store.list()
    # FIFO order when priorities are equal (created_at ordering).
    assert [g.objective for g in items] == ["A", "B"]
    assert q1.queue_id != q2.queue_id


def test_pop_returns_highest_priority(store: GoalQueueStore) -> None:
    store.enqueue("low", priority=0)
    store.enqueue("high", priority=10)
    store.enqueue("mid", priority=5)
    popped = store.pop()
    assert popped is not None
    assert popped.objective == "high"


def test_pop_fifo_within_same_priority(store: GoalQueueStore) -> None:
    store.enqueue("first")
    store.enqueue("second")
    popped = store.pop()
    assert popped is not None
    assert popped.objective == "first"


def test_remove_by_id(store: GoalQueueStore) -> None:
    q = store.enqueue("do thing")
    assert store.remove(q.queue_id) is True
    assert len(store) == 0
    assert store.remove(q.queue_id) is False  # idempotent miss


def test_clear(store: GoalQueueStore) -> None:
    store.enqueue("A")
    store.enqueue("B")
    store.clear()
    assert len(store) == 0
    # Second clear is a no-op.
    store.clear()
    assert len(store) == 0


def test_persist_and_restore(metadata: dict, store: GoalQueueStore) -> None:
    store.enqueue("Ship feature X", priority=5)
    store.enqueue("Write tests")
    # Rebuild from the same metadata — must restore the queue.
    rebuilt = GoalQueueStore(metadata)
    items = rebuilt.list()
    assert [g.objective for g in items] == ["Ship feature X", "Write tests"]
    assert items[0].priority == 5


def test_persistence_key_matches_enum(metadata: dict, store: GoalQueueStore) -> None:
    store.enqueue("A")
    # The store persists to the "goal_queue_state" key, NOT "goal_queue"
    # (the runtime handle lives at the latter).
    from openharness.engine.types import ToolMetadataKey

    persistence_key = ToolMetadataKey.GOAL_QUEUE.value
    assert persistence_key == "goal_queue_state"
    assert persistence_key in metadata


def test_enqueue_length_limit() -> None:
    store = GoalQueueStore({}, max_length=2)
    store.enqueue("A")
    store.enqueue("B")
    with pytest.raises(ValueError):
        store.enqueue("C")


def test_enqueue_empty_objective_rejected(store: GoalQueueStore) -> None:
    with pytest.raises(ValueError):
        store.enqueue("")
    with pytest.raises(ValueError):
        store.enqueue("   ")


def test_restore_to_head_inserts_at_index_0(store: GoalQueueStore) -> None:
    q1 = store.enqueue("first")
    q2 = store.enqueue("second")
    popped = store.pop()
    assert popped is not None and popped.queue_id == q1.queue_id
    store.restore_to_head(popped)
    items = store.list()
    assert [g.queue_id for g in items] == [q1.queue_id, q2.queue_id]


def test_restore_to_head_is_idempotent(store: GoalQueueStore) -> None:
    q = store.enqueue("only one")
    store.pop()
    store.restore_to_head(q)
    store.restore_to_head(q)  # second call must not duplicate
    assert len(store) == 1


def test_restore_to_head_preserves_existing_order(store: GoalQueueStore) -> None:
    q = store.enqueue("still in queue")
    # Try to "restore" a goal that is already there — must be a no-op.
    store.restore_to_head(q)
    assert len(store) == 1


def test_corrupt_queue_entries_dropped_on_restore(metadata: dict) -> None:
    metadata[GOAL_QUEUE_KEY] = [
        {"queue_id": "x", "objective": "valid"},
        "not a dict",                       # dropped (not a dict)
        {"queue_id": "y", "objective": "also valid"},
    ]
    store = GoalQueueStore(metadata)
    items = store.list()
    objectives = {g.objective for g in items}
    assert "valid" in objectives
    assert "also valid" in objectives
    # Only the garbage "not a dict" entry is dropped.
    assert len(items) == 2


def test_start_next_from_queue_creates_goal(metadata: dict, store: GoalQueueStore) -> None:
    metadata[GOAL_QUEUE_KEY] = store
    gm = GoalMode(metadata)
    store.enqueue("Ship feature X")
    snap = gm.start_next_from_queue(store)
    assert snap is not None
    assert snap.objective == "Ship feature X"
    assert len(store) == 0
    assert gm.get_goal() is not None


def test_start_next_from_queue_replaces_residual_state(metadata: dict, store: GoalQueueStore) -> None:
    """If a residual record is still present (e.g. blocked goal awaiting
    auto-advance), start_next_from_queue replaces it — no rollback."""
    metadata[GOAL_QUEUE_KEY] = store
    gm = GoalMode(metadata)
    gm.create_goal("already active")
    gm.mark_blocked(reason="stuck")
    store.enqueue("queued")
    snap = gm.start_next_from_queue(store)
    assert snap is not None
    assert snap.objective == "queued"
    assert len(store) == 0
    assert gm.get_goal() is not None
    assert gm.get_goal().objective == "queued"


def test_start_next_from_queue_restores_on_failure(metadata: dict, store: GoalQueueStore) -> None:
    metadata[GOAL_QUEUE_KEY] = store
    gm = GoalMode(metadata)
    # Queue an objective that is too long for create_goal — must rollback.
    too_long = "x" * 5000
    store.enqueue(too_long)
    snap = gm.start_next_from_queue(store)
    assert snap is None
    assert len(store) == 1  # restored to head
    assert store.peek().objective == too_long


def test_start_next_from_queue_returns_none_on_empty(metadata: dict, store: GoalQueueStore) -> None:
    gm = GoalMode(metadata)
    assert gm.start_next_from_queue(store) is None


def test_start_next_from_queue_handles_none_queue(metadata: dict) -> None:
    gm = GoalMode(metadata)
    # None queue → graceful no-op, no exception.
    assert gm.start_next_from_queue(None) is None


def test_budget_limits_preserved_through_queue(metadata: dict, store: GoalQueueStore) -> None:
    limits = GoalBudgetLimits(turn_budget=20)
    store.enqueue("A", budget_limits=limits)
    popped = store.pop()
    assert popped is not None
    assert popped.budget_limits.turn_budget == 20
