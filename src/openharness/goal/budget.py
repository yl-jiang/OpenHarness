"""Budget helpers for goal mode.

Normalizes raw ``SetGoalBudget`` tool arguments into a ``GoalBudgetLimits``
value. Invalid inputs (non-positive, out-of-range) return ``None`` so callers
can produce a user-friendly error rather than silently clamping.
"""

from __future__ import annotations

from typing import Optional

from openharness.goal.state import GoalBudgetLimits

# Time-budget sanity bounds. Anything outside is rejected as unreasonable.
_MIN_TIME_SECONDS = 1
_MAX_TIME_SECONDS = 24 * 60 * 60  # 24 hours

_TIME_UNITS_TO_MS: dict[str, int] = {
    "seconds": 1000,
    "minutes": 60 * 1000,
    "hours": 60 * 60 * 1000,
}

_COUNT_UNITS = {"turns", "tokens"}


def normalize_budget_input(value: float, unit: str) -> tuple[int | float, str]:
    """Normalize a raw budget value.

    - Count units (``turns``, ``tokens``): rounded, clamped to >= 1.
    - Time units: returned unchanged in milliseconds; caller should run
      ``budget_limits_from_input`` for range validation.
    """
    if unit in _COUNT_UNITS:
        normalized = max(1, round(value))
        return normalized, unit
    if unit in _TIME_UNITS_TO_MS:
        return value, unit
    raise ValueError(f"Unknown budget unit: {unit!r}")


def budget_limits_from_input(value: float, unit: str) -> Optional[GoalBudgetLimits]:
    """Build a ``GoalBudgetLimits`` from a tool call's value+unit.

    Returns ``None`` for unreasonable time budgets (< 1s or > 24h). Count
    budgets are normalized via ``normalize_budget_input``.
    """
    normalized, norm_unit = normalize_budget_input(value, unit)

    if norm_unit == "turns":
        return GoalBudgetLimits(turn_budget=int(normalized))
    if norm_unit == "tokens":
        return GoalBudgetLimits(token_budget=int(normalized))

    # Time unit: convert raw value to ms, then validate range in seconds.
    ms = normalized * _TIME_UNITS_TO_MS[norm_unit]
    seconds = ms / 1000.0
    if seconds < _MIN_TIME_SECONDS or seconds > _MAX_TIME_SECONDS:
        return None
    return GoalBudgetLimits(wall_clock_budget_ms=int(round(ms)))