"""Tests for GoalSettings (Phase 7 §15.1)."""

from __future__ import annotations

from openharness.config.settings import GoalSettings, Settings
from openharness.goal.state import GoalBudgetLimits, GoalMode


def test_default_settings_values() -> None:
    s = GoalSettings()
    assert s.enabled is True
    assert s.max_objective_length == 4000
    assert s.default_turn_budget is None
    assert s.default_token_budget is None
    assert s.default_wall_clock_budget_s is None
    assert s.auto_advance_on_blocked is False
    assert s.restore_permission_after_goal is False
    assert s.hard_cap_iterations == 200
    assert s.max_queue_length == 50


def test_settings_yaml_load() -> None:
    # Settings() default-instantiates a GoalSettings via the Field default_factory.
    settings = Settings()
    assert isinstance(settings.goal, GoalSettings)
    assert settings.goal.restore_permission_after_goal is False


def test_goal_settings_from_kwargs() -> None:
    s = GoalSettings(
        default_turn_budget=50,
        restore_permission_after_goal=True,
        max_queue_length=25,
    )
    assert s.default_turn_budget == 50
    assert s.restore_permission_after_goal is True
    assert s.max_queue_length == 25


def test_default_budget_applied_on_create() -> None:
    """Users can apply settings.goal.default_*_budget to a fresh goal.

    The contract is explicit on the caller side (registry.py /goal handler
    or driver) — GoalMode itself does not know about settings. This test
    just demonstrates the intended usage pattern.
    """
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing")
    # Simulate what the /goal handler would do with settings.goal defaults:
    defaults = GoalBudgetLimits(turn_budget=50, token_budget=100_000)
    gm.set_budget_limits(defaults)
    snapshot = gm.get_goal()
    assert snapshot is not None
    assert snapshot.budget.turn_budget == 50
    assert snapshot.budget.token_budget == 100_000


def test_user_explicit_budget_overrides_default() -> None:
    """If the user calls SetGoalBudget with a different value, that wins."""
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing")
    # Default applied by handler
    gm.set_budget_limits(GoalBudgetLimits(turn_budget=50))
    # User override via tool
    gm.set_budget_limits(GoalBudgetLimits(turn_budget=10))
    snapshot = gm.get_goal()
    assert snapshot is not None
    assert snapshot.budget.turn_budget == 10


def test_original_permission_mode_recorded_on_create() -> None:
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing", original_permission_mode="default")
    assert gm.original_permission_mode() == "default"


def test_original_permission_mode_persists_across_restart() -> None:
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing", original_permission_mode="plan")
    # Simulate a process restart: build a fresh GoalMode over the same dict.
    restored = GoalMode(metadata)
    assert restored.original_permission_mode() == "plan"


def test_original_permission_mode_none_by_default() -> None:
    metadata: dict = {}
    gm = GoalMode(metadata)
    gm.create_goal("do thing")
    assert gm.original_permission_mode() is None


def test_hard_cap_iterations_from_settings() -> None:
    # The driver currently hardcodes 200 — this test asserts the setting
    # exposes the same default so Phase 6/7 can wire it through.
    s = GoalSettings()
    assert s.hard_cap_iterations == 200
