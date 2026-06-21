"""Goal mode state machine for multi-turn autonomous execution.

A single active goal is tracked at a time. State is serialized into
``tool_metadata["goal_state"]`` so it survives across turns and (when the
session backend persists tool_metadata) across process restarts.

Only three durable statuses exist: ``active``, ``paused``, ``blocked``.
``complete`` is a transient event, not a persistent status — once a goal is
marked complete the record is cleared and ``snapshot is None`` afterwards.
``cancel`` is a deletion action, not a status.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

from openharness.engine.types import ToolMetadataKey

logger = logging.getLogger(__name__)

# Keys inside tool_metadata. Exposed so tools and the driver can reference them.
GOAL_STATE_KEY: str = ToolMetadataKey.GOAL_STATE.value  # "goal_state"
GOAL_MODE_KEY: str = "goal_mode"  # runtime handle, not persisted

# Durable statuses. "complete" is transient and never stored.
DurableStatus = Literal["active", "paused", "blocked"]
_DURABLE_STATUSES: frozenset[str] = frozenset({"active", "paused", "blocked"})

# Upper bound on the objective length. Matches kimi-code (4000 chars).
MAX_GOAL_OBJECTIVE_LENGTH = 4000


@dataclass
class GoalBudgetLimits:
    """Hard budget caps set by the user or model. All fields optional."""

    turn_budget: Optional[int] = None
    token_budget: Optional[int] = None
    wall_clock_budget_ms: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "GoalBudgetLimits":
        if not data:
            return cls()
        return cls(
            turn_budget=data.get("turn_budget"),
            token_budget=data.get("token_budget"),
            wall_clock_budget_ms=data.get("wall_clock_budget_ms"),
        )


@dataclass
class GoalState:
    """Mutable goal record held in tool_metadata.

    ``status`` is constrained to ``active | paused | blocked``. ``complete``
    and ``cancel`` are actions that clear this record rather than setting a
    persistent status.
    """

    goal_id: str
    objective: str
    completion_criterion: Optional[str] = None
    status: str = "active"
    last_actor: Optional[str] = None
    turns_used: int = 0
    tokens_used: int = 0
    wall_clock_ms: int = 0
    wall_clock_resumed_at: Optional[float] = None
    budget_limits: GoalBudgetLimits = field(default_factory=GoalBudgetLimits)
    terminal_reason: Optional[str] = None
    # Permission mode *before* this goal started (so the driver can opt-in
    # to restoring it when the goal ends). None means "not recorded".
    original_permission_mode: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget_limits"] = self.budget_limits.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GoalState":
        # Require a string objective — anything else is treated as corrupt.
        objective = data.get("objective")
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("GoalState.from_dict: missing or empty objective")
        budget_data = data.get("budget_limits")
        budget = GoalBudgetLimits.from_dict(budget_data) if budget_data else GoalBudgetLimits()
        original_perm = data.get("original_permission_mode")
        if original_perm is not None and not isinstance(original_perm, str):
            original_perm = None
        return cls(
            goal_id=str(data.get("goal_id") or uuid.uuid4()),
            objective=objective,
            completion_criterion=data.get("completion_criterion"),
            status=str(data.get("status", "active")),
            last_actor=data.get("last_actor"),
            turns_used=int(data.get("turns_used", 0)),
            tokens_used=int(data.get("tokens_used", 0)),
            wall_clock_ms=int(data.get("wall_clock_ms", 0)),
            wall_clock_resumed_at=data.get("wall_clock_resumed_at"),
            budget_limits=budget,
            terminal_reason=data.get("terminal_reason"),
            original_permission_mode=original_perm,
        )


@dataclass(frozen=True)
class GoalBudgetReport:
    """Read-only view of budget usage for a goal snapshot."""

    token_budget: Optional[int]
    turn_budget: Optional[int]
    wall_clock_budget_ms: Optional[int]

    remaining_tokens: Optional[int]
    remaining_turns: Optional[int]
    remaining_wall_clock_ms: Optional[int]

    token_budget_reached: bool
    turn_budget_reached: bool
    wall_clock_budget_reached: bool

    over_budget: bool
    usage_fraction: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GoalSnapshot:
    """Immutable, read-only view of a goal at a point in time.

    Returned to tools, driver, and UI events. Safe to expose in events and
    to serialize because it contains no mutable references.
    """

    goal_id: str
    objective: str
    completion_criterion: Optional[str]
    status: str
    turns_used: int
    tokens_used: int
    wall_clock_ms: int
    budget: GoalBudgetReport
    terminal_reason: Optional[str]
    last_actor: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["budget"] = self.budget.to_dict()
        return data


def _now_ms() -> float:
    return time.time() * 1000.0


def _build_budget_report(state: GoalState) -> GoalBudgetReport:
    """Compute a read-only budget report from current state + limits."""
    limits = state.budget_limits

    remaining_turns: Optional[int] = None
    turn_reached = False
    if limits.turn_budget is not None:
        remaining_turns = max(0, limits.turn_budget - state.turns_used)
        turn_reached = state.turns_used >= limits.turn_budget

    remaining_tokens: Optional[int] = None
    token_reached = False
    if limits.token_budget is not None:
        remaining_tokens = max(0, limits.token_budget - state.tokens_used)
        token_reached = state.tokens_used >= limits.token_budget

    remaining_wall: Optional[int] = None
    wall_reached = False
    if limits.wall_clock_budget_ms is not None:
        elapsed = state.wall_clock_ms
        # If currently active and has a resume timestamp, count live elapsed.
        if state.status == "active" and state.wall_clock_resumed_at is not None:
            elapsed += int(_now_ms() - state.wall_clock_resumed_at)
        remaining_wall = max(0, limits.wall_clock_budget_ms - elapsed)
        wall_reached = elapsed >= limits.wall_clock_budget_ms

    # usage_fraction = max(used / total) across set budgets. used/total, not
    # remaining/total, so higher == closer to budget exhaustion.
    fractions: list[float] = []
    if limits.turn_budget and limits.turn_budget > 0:
        fractions.append(state.turns_used / limits.turn_budget)
    if limits.token_budget and limits.token_budget > 0:
        fractions.append(state.tokens_used / limits.token_budget)
    if limits.wall_clock_budget_ms and limits.wall_clock_budget_ms > 0:
        used = state.wall_clock_ms
        if state.status == "active" and state.wall_clock_resumed_at is not None:
            used += int(_now_ms() - state.wall_clock_resumed_at)
        fractions.append(used / limits.wall_clock_budget_ms)
    usage_fraction = max(fractions) if fractions else 0.0

    return GoalBudgetReport(
        token_budget=limits.token_budget,
        turn_budget=limits.turn_budget,
        wall_clock_budget_ms=limits.wall_clock_budget_ms,
        remaining_tokens=remaining_tokens,
        remaining_turns=remaining_turns,
        remaining_wall_clock_ms=remaining_wall,
        token_budget_reached=token_reached,
        turn_budget_reached=turn_reached,
        wall_clock_budget_reached=wall_reached,
        over_budget=turn_reached or token_reached or wall_reached,
        usage_fraction=usage_fraction,
    )


def _snapshot_from_state(state: GoalState) -> GoalSnapshot:
    wall = state.wall_clock_ms
    if state.status == "active" and state.wall_clock_resumed_at is not None:
        wall += int(_now_ms() - state.wall_clock_resumed_at)
    return GoalSnapshot(
        goal_id=state.goal_id,
        objective=state.objective,
        completion_criterion=state.completion_criterion,
        status=state.status,
        turns_used=state.turns_used,
        tokens_used=state.tokens_used,
        wall_clock_ms=wall,
        budget=_build_budget_report(state),
        terminal_reason=state.terminal_reason,
        last_actor=state.last_actor,
    )


class GoalMode:
    """Single-goal lifecycle manager.

    Holds the mutable ``GoalState`` and keeps it in sync with the
    ``tool_metadata`` dict passed at construction. The driver and tools access
    this manager via ``tool_metadata["goal_mode"]``; serialized state lives at
    ``tool_metadata["goal_state"]`` so it survives session persistence.

    Optionally accepts a ``hook_executor``; state transitions enqueue events
    that the driver flushes once per turn (see :meth:`flush_hooks`).
    """

    def __init__(
        self,
        tool_metadata: dict[str, Any],
        *,
        hook_executor: Any | None = None,
    ) -> None:
        self._metadata = tool_metadata
        self._hook_executor = hook_executor
        self._pending_hooks: list[tuple[Any, dict[str, Any]]] = []
        self._state: Optional[GoalState] = self._restore_from_metadata()

    # ------------------------------------------------------------------ reads

    def get_goal(self) -> Optional[GoalSnapshot]:
        """Return the current goal snapshot, or None if no goal exists."""
        if self._state is None:
            return None
        return _snapshot_from_state(self._state)

    def get_active_goal(self) -> Optional[GoalSnapshot]:
        """Return the snapshot only when the goal is currently active."""
        if self._state is None or self._state.status != "active":
            return None
        return _snapshot_from_state(self._state)

    def original_permission_mode(self) -> Optional[str]:
        """Return the permission mode recorded when the goal was created.

        Used by the driver (Phase 7) to decide whether to restore the
        pre-goal permission mode when the goal ends.
        """
        if self._state is None:
            return None
        return self._state.original_permission_mode

    # -------------------------------------------------------------- lifecycle

    def create_goal(
        self,
        objective: str,
        *,
        completion_criterion: Optional[str] = None,
        replace: bool = False,
        actor: str = "model",
        original_permission_mode: Optional[str] = None,
    ) -> GoalSnapshot:
        """Create a new goal. If one already exists, require ``replace=True``.

        Raises ``ValueError`` for empty or too-long objectives and when a goal
        exists and ``replace`` is False.
        """
        if not objective or not objective.strip():
            raise ValueError("Goal objective must not be empty.")
        if len(objective) > MAX_GOAL_OBJECTIVE_LENGTH:
            raise ValueError(
                f"Goal objective too long (max {MAX_GOAL_OBJECTIVE_LENGTH} chars)."
            )
        if self._state is not None and not replace:
            raise ValueError(
                "A goal is already active. Pass replace=True to replace it."
            )

        self._state = GoalState(
            goal_id=str(uuid.uuid4()),
            objective=objective.strip(),
            completion_criterion=completion_criterion,
            status="active",
            last_actor=actor,
            wall_clock_resumed_at=_now_ms(),
            original_permission_mode=original_permission_mode,
        )
        self._persist()
        snapshot = _snapshot_from_state(self._state)
        self._enqueue_hook_event("goal_created", snapshot, actor=actor)
        return snapshot

    def pause_goal(
        self,
        *,
        reason: Optional[str] = None,
        actor: str = "user",
    ) -> GoalSnapshot:
        """Pause the current goal. Raises if there is no goal."""
        state = self._require_state()
        self._accumulate_wall_clock(state)
        state.status = "paused"
        state.last_actor = actor
        state.terminal_reason = reason
        state.wall_clock_resumed_at = None
        self._persist()
        snapshot = _snapshot_from_state(state)
        self._enqueue_hook_event("goal_paused", snapshot, reason=reason, actor=actor)
        return snapshot

    def resume_goal(
        self,
        *,
        reason: Optional[str] = None,
        actor: str = "user",
    ) -> GoalSnapshot:
        """Resume a paused or blocked goal. Raises if no goal or already active."""
        state = self._require_state()
        if state.status == "active":
            # Idempotent: already active, nothing to do beyond returning snapshot.
            return _snapshot_from_state(state)
        state.status = "active"
        state.last_actor = actor
        state.terminal_reason = reason
        state.wall_clock_resumed_at = _now_ms()
        self._persist()
        snapshot = _snapshot_from_state(state)
        self._enqueue_hook_event("goal_resumed", snapshot, reason=reason, actor=actor)
        return snapshot

    def cancel_goal(self, *, actor: str = "user") -> Optional[GoalSnapshot]:
        """Cancel and delete the current goal record. Returns the last snapshot."""
        if self._state is None:
            return None
        self._accumulate_wall_clock(self._state)
        snapshot = _snapshot_from_state(self._state)
        self._state = None
        self._persist()
        self._enqueue_hook_event("goal_cancelled", snapshot, actor=actor)
        return snapshot

    # ------------------------------------------------------- terminal outcomes

    def mark_complete(
        self,
        *,
        reason: Optional[str] = None,
        actor: str = "model",
    ) -> Optional[GoalSnapshot]:
        """Mark the goal complete.

        Persists ``status="complete"`` so the driver can read the snapshot,
        inject a summary prompt, and run a final turn. The driver is
        responsible for calling :meth:`clear_after_complete` when done.

        If the session is restored with ``status="complete"`` still set
        (crash before the driver cleared it), ``_restore_from_metadata``
        treats that as absent — ``complete`` is transient by design.
        """
        if self._state is None:
            return None
        self._accumulate_wall_clock(self._state)
        self._state.status = "complete"
        self._state.last_actor = actor
        self._state.terminal_reason = reason
        self._persist()
        snapshot = _snapshot_from_state(self._state)
        self._enqueue_hook_event(
            "goal_completed", snapshot, reason=reason, actor=actor
        )
        return snapshot

    def clear_after_complete(self) -> None:
        """Delete the goal record after the driver has finished the summary turn.

        Idempotent: safe to call even if no goal is present or the status
        is not "complete".
        """
        self._state = None
        self._persist()

    # ------------------------------------------------------------ queue bridge

    def start_next_from_queue(self, queue: Any) -> Optional[GoalSnapshot]:
        """Pop the next queued goal and create it.

        Failure rollback (kimi-code alignment, design doc §14.7.4): if
        ``create_goal`` raises (e.g. objective too long after trimming),
        the popped item is put back at the queue head via
        ``queue.restore_to_head`` so it is not silently lost.

        Passes ``replace=True`` so a residual record from the previous
        goal (e.g. blocked, still present when auto-advancing) does not
        reject the promotion.

        Returns the new active snapshot, or ``None`` if the queue was empty.
        """
        # Duck-type the queue so we do not need a hard import (avoids
        # coupling GoalMode to goal.queue at module load).
        if queue is None or not hasattr(queue, "pop") or not hasattr(queue, "restore_to_head"):
            return None
        queued = queue.pop()
        if queued is None:
            return None
        try:
            return self.create_goal(
                queued.objective,
                completion_criterion=getattr(queued, "completion_criterion", None),
                actor="runtime",
                replace=True,
            )
        except Exception:
            # Rollback: put the popped item back at the head so the next
            # retry sees the same objective.
            queue.restore_to_head(queued)
            logger.exception("Failed to promote queued goal; rolled back")
            return None

    def mark_blocked(
        self,
        *,
        reason: Optional[str] = None,
        actor: str = "runtime",
    ) -> Optional[GoalSnapshot]:
        """Mark the goal blocked (e.g. budget exhausted, external blocker)."""
        if self._state is None:
            return None
        self._accumulate_wall_clock(self._state)
        self._state.status = "blocked"
        self._state.last_actor = actor
        self._state.terminal_reason = reason
        self._state.wall_clock_resumed_at = None
        self._persist()
        snapshot = _snapshot_from_state(self._state)
        self._enqueue_hook_event(
            "goal_blocked", snapshot, reason=reason, actor=actor
        )
        return snapshot

    # ------------------------------------------------------------- accounting

    def record_token_usage(self, token_delta: int) -> Optional[GoalSnapshot]:
        """Add ``token_delta`` to the running total. No-op if no goal."""
        if self._state is None:
            return None
        if token_delta and token_delta > 0:
            self._state.tokens_used += int(token_delta)
            self._persist()
        return _snapshot_from_state(self._state)

    def increment_turn(self) -> Optional[GoalSnapshot]:
        """Add one to the turn counter. No-op if no goal."""
        if self._state is None:
            return None
        self._state.turns_used += 1
        self._persist()
        return _snapshot_from_state(self._state)

    # ---------------------------------------------------------------- budget

    def set_budget_limits(self, limits: GoalBudgetLimits) -> GoalSnapshot:
        """Apply hard budget caps to the current goal."""
        state = self._require_state()
        state.budget_limits = limits
        self._persist()
        return _snapshot_from_state(state)

    # ------------------------------------------------ session recovery helper

    def normalize_after_replay(self) -> None:
        """Downgrade an ``active`` goal to ``paused`` after a process restart.

        Active state cannot survive a restart — the driver loop is gone. Any
        replayed ``active`` goal is treated as user-pausable state.
        """
        if self._state is None:
            return
        if self._state.status == "active":
            self._state.status = "paused"
            self._state.last_actor = "runtime"
            self._state.terminal_reason = "Paused after agent resume"
            self._state.wall_clock_resumed_at = None
            self._persist()

    # ------------------------------------------------------------ internals

    def _require_state(self) -> GoalState:
        if self._state is None:
            raise ValueError("No current goal.")
        return self._state

    def _accumulate_wall_clock(self, state: GoalState) -> None:
        """Fold elapsed wall-clock time since last resume into the total."""
        if state.status == "active" and state.wall_clock_resumed_at is not None:
            state.wall_clock_ms += int(_now_ms() - state.wall_clock_resumed_at)
            state.wall_clock_resumed_at = None

    def _persist(self) -> None:
        """Serialize current state to tool_metadata (or drop the key)."""
        if self._state is None:
            self._metadata.pop(GOAL_STATE_KEY, None)
            return
        self._metadata[GOAL_STATE_KEY] = self._state.to_dict()

    # --------------------------------------------------------------- hooks

    def _enqueue_hook_event(
        self,
        event_name: str,
        snapshot: Optional[GoalSnapshot],
        *,
        reason: Optional[str] = None,
        actor: Optional[str] = None,
    ) -> None:
        """Append a hook event to the pending queue (flushed by driver).

        The queue is in-memory only; a process restart drops pending events
        (hook events are informational, losing them is not a correctness issue).
        """
        if snapshot is None:
            return
        payload: dict[str, Any] = {
            "event": event_name,
            "goal": snapshot.to_dict(),
        }
        if reason is not None:
            payload["reason"] = reason
        if actor is not None:
            payload["actor"] = actor
        # Lazy import to avoid coupling GoalMode to the hooks package at
        # import time; only pay the cost when hooks are actually configured.
        from openharness.hooks import HookEvent

        try:
            event = HookEvent(event_name)
        except ValueError:
            # Unknown event name (should not happen) — drop silently.
            return
        self._pending_hooks.append((event, payload))

    async def flush_hooks(self) -> None:
        """Dispatch queued hook events. Safe to call with no executor."""
        if not self._pending_hooks:
            return
        pending = self._pending_hooks
        self._pending_hooks = []
        if self._hook_executor is None:
            return
        for event, payload in pending:
            try:
                await self._hook_executor.execute(event, payload)
            except Exception:
                # Hooks are informational; a failing hook must not abort the
                # driver. Log-and-continue is the contract.
                logger.exception("goal hook %s failed", event.value)

    def _restore_from_metadata(self) -> Optional[GoalState]:
        raw = self._metadata.get(GOAL_STATE_KEY)
        if not raw or not isinstance(raw, dict):
            return None
        try:
            state = GoalState.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("Discarding corrupt goal_state: %s", exc)
            self._metadata.pop(GOAL_STATE_KEY, None)
            return None
        if state.status not in _DURABLE_STATUSES:
            # "complete" is transient — if replayed from a snapshot, treat as
            # absent. Unknown statuses are also discarded for safety.
            logger.warning("Discarding goal_state with non-durable status: %r", state.status)
            self._metadata.pop(GOAL_STATE_KEY, None)
            return None
        return state
