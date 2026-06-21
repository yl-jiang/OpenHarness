"""Goal queue: upcoming goals waiting to be promoted when the current one ends.

Persistence: the queue lives at ``tool_metadata["goal_queue"]`` alongside
``goal_state``. Session restore brings the queue back intact; active goals
are separately downgraded to paused by ``GoalMode.normalize_after_replay``.

Failure rollback: ``restore_to_head`` (kimi-code ``restoreGoalQueueItem``
equivalent) puts a popped item back at position 0 when ``create_goal``
throws, so a bad objective does not silently vanish from the queue.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict, dataclass, field
from time import time
from typing import Any, Optional

from openharness.engine.types import ToolMetadataKey
from openharness.goal.state import GoalBudgetLimits

logger = logging.getLogger(__name__)

GOAL_QUEUE_KEY: str = ToolMetadataKey.GOAL_QUEUE.value  # "goal_queue"
MAX_QUEUE_LENGTH = 50  # default; overridable via settings


@dataclass
class QueuedGoal:
    """A goal waiting in the queue.

    By design (see design doc §14.7.4) queue items are *lightweight*: only
    ``objective`` is required. ``completion_criterion`` and ``budget_limits``
    may be pre-bound by the user (via explicit CLI flags in later phases)
    but default to ``None`` / empty, so the model sets them after promotion
    via the ``SetGoalBudget`` tool and the reminder prompt.
    """

    queue_id: str
    objective: str
    completion_criterion: Optional[str] = None
    budget_limits: GoalBudgetLimits = field(default_factory=GoalBudgetLimits)
    priority: int = 0
    created_at: float = field(default_factory=time)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget_limits"] = self.budget_limits.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueuedGoal":
        budget_data = data.get("budget_limits")
        budget = GoalBudgetLimits.from_dict(budget_data) if budget_data else GoalBudgetLimits()
        return cls(
            queue_id=str(data.get("queue_id") or uuid.uuid4()),
            objective=str(data.get("objective", "")),
            completion_criterion=data.get("completion_criterion"),
            budget_limits=budget,
            priority=int(data.get("priority", 0)),
            created_at=float(data.get("created_at", time())),
        )


class GoalQueueStore:
    """In-memory queue backed by ``tool_metadata["goal_queue"]``.

    Ordering: highest ``priority`` first; ties broken by earliest
    ``created_at``. Mutations re-persist on every call.
    """

    def __init__(
        self,
        tool_metadata: dict[str, Any],
        *,
        max_length: int = MAX_QUEUE_LENGTH,
    ) -> None:
        self._metadata = tool_metadata
        self._max_length = max_length
        self._items: list[QueuedGoal] = self._restore()

    # ------------------------------------------------------------------ reads

    def peek(self) -> Optional[QueuedGoal]:
        """Return the next item without removing it."""
        return self._items[0] if self._items else None

    def list(self) -> list[QueuedGoal]:
        """Return a defensive copy of the queue (already sorted)."""
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    # -------------------------------------------------------------- mutators

    def enqueue(
        self,
        objective: str,
        *,
        priority: int = 0,
        completion_criterion: Optional[str] = None,
        budget_limits: Optional[GoalBudgetLimits] = None,
    ) -> QueuedGoal:
        """Add a goal to the queue. Raises ``ValueError`` on overflow or bad input."""
        if not objective or not objective.strip():
            raise ValueError("Queue objective must not be empty.")
        if len(self._items) >= self._max_length:
            raise ValueError(
                f"Goal queue is full (max {self._max_length}). "
                "Remove items with `/goal queue remove <id>`."
            )
        goal = QueuedGoal(
            queue_id=str(uuid.uuid4()),
            objective=objective.strip(),
            completion_criterion=completion_criterion,
            budget_limits=budget_limits or GoalBudgetLimits(),
            priority=priority,
        )
        self._items.append(goal)
        self._sort_in_place()
        self._persist()
        return goal

    def pop(self) -> Optional[QueuedGoal]:
        """Remove and return the highest-priority item (or None if empty)."""
        if not self._items:
            return None
        goal = self._items.pop(0)
        self._persist()
        return goal

    def remove(self, queue_id: str) -> bool:
        """Remove an item by id. Returns True if found."""
        before = len(self._items)
        self._items = [g for g in self._items if g.queue_id != queue_id]
        if len(self._items) == before:
            return False
        self._persist()
        return True

    def clear(self) -> None:
        """Drop all items."""
        if not self._items:
            return
        self._items.clear()
        self._persist()

    def restore_to_head(self, goal: QueuedGoal) -> None:
        """Put a previously-popped item back at position 0.

        Idempotent: if ``goal.queue_id`` is already in the queue, do nothing.
        This mirrors kimi-code's ``restoreGoalQueueItem`` and guarantees a
        failed promotion does not silently lose the queued goal.
        """
        if any(g.queue_id == goal.queue_id for g in self._items):
            return
        self._items.insert(0, goal)
        self._persist()

    # ------------------------------------------------------------- internals

    def _sort_in_place(self) -> None:
        # Highest priority first; ties broken by earliest created_at.
        self._items.sort(key=lambda g: (-g.priority, g.created_at))

    def _persist(self) -> None:
        self._metadata[GOAL_QUEUE_KEY] = [g.to_dict() for g in self._items]

    def _restore(self) -> list[QueuedGoal]:
        raw = self._metadata.get(GOAL_QUEUE_KEY)
        if not raw or not isinstance(raw, list):
            return []
        items: list[QueuedGoal] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                items.append(QueuedGoal.from_dict(entry))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Dropping corrupt queue entry: %s", exc)
        # Re-sort defensively (in case priorities changed across versions).
        items.sort(key=lambda g: (-g.priority, g.created_at))
        return items
