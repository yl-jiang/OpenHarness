"""Tests for goal/budget.py helpers."""

from __future__ import annotations

import pytest

from openharness.goal.budget import budget_limits_from_input, normalize_budget_input
from openharness.goal.state import GoalBudgetLimits


def test_normalize_turns() -> None:
    value, unit = normalize_budget_input(3.7, "turns")
    assert value == 4
    assert unit == "turns"


def test_normalize_turns_clamps_to_one() -> None:
    value, _ = normalize_budget_input(0.2, "turns")
    assert value == 1


def test_normalize_tokens() -> None:
    value, unit = normalize_budget_input(500.4, "tokens")
    assert value == 500
    assert unit == "tokens"


def test_normalize_unknown_unit_raises() -> None:
    with pytest.raises(ValueError):
        normalize_budget_input(1.0, "apples")


def test_budget_limits_turns() -> None:
    limits = budget_limits_from_input(5, "turns")
    assert limits == GoalBudgetLimits(turn_budget=5)


def test_budget_limits_tokens() -> None:
    limits = budget_limits_from_input(1000, "tokens")
    assert limits == GoalBudgetLimits(token_budget=1000)


def test_budget_limits_time_valid() -> None:
    limits = budget_limits_from_input(5, "minutes")
    assert limits is not None
    assert limits.wall_clock_budget_ms == 5 * 60 * 1000
    assert limits.turn_budget is None
    assert limits.token_budget is None


def test_budget_limits_hours() -> None:
    limits = budget_limits_from_input(2, "hours")
    assert limits is not None
    assert limits.wall_clock_budget_ms == 2 * 60 * 60 * 1000


def test_budget_limits_seconds() -> None:
    limits = budget_limits_from_input(30, "seconds")
    assert limits is not None
    assert limits.wall_clock_budget_ms == 30_000


def test_budget_limits_time_too_short() -> None:
    # 500 ms < 1 second — rejected.
    assert budget_limits_from_input(0.5, "seconds") is None


def test_budget_limits_time_too_long() -> None:
    # 25 hours > 24 hours — rejected.
    assert budget_limits_from_input(25, "hours") is None


def test_budget_limits_time_boundary_low() -> None:
    # Exactly 1 second — accepted.
    limits = budget_limits_from_input(1, "seconds")
    assert limits == GoalBudgetLimits(wall_clock_budget_ms=1000)


def test_budget_limits_time_boundary_high() -> None:
    # Exactly 24 hours — accepted.
    limits = budget_limits_from_input(24, "hours")
    assert limits == GoalBudgetLimits(wall_clock_budget_ms=24 * 60 * 60 * 1000)
