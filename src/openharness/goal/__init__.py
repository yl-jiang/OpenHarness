"""Goal mode package.

Public API:

- ``GoalMode``: lifecycle manager held in ``tool_metadata["goal_mode"]``
- ``GoalState`` / ``GoalSnapshot`` / ``GoalBudgetReport`` / ``GoalBudgetLimits``:
  state and view dataclasses
- Constants ``GOAL_STATE_KEY`` and ``GOAL_MODE_KEY`` for metadata access
"""

from openharness.goal.state import (
    GOAL_MODE_KEY,
    GOAL_STATE_KEY,
    GoalBudgetLimits,
    GoalBudgetReport,
    GoalMode,
    GoalSnapshot,
    GoalState,
)

__all__ = [
    "GOAL_MODE_KEY",
    "GOAL_STATE_KEY",
    "GoalBudgetLimits",
    "GoalBudgetReport",
    "GoalMode",
    "GoalSnapshot",
    "GoalState",
]
