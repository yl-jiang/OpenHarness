"""Tests for review completion counter in AppState and StatusBar integration."""

from __future__ import annotations

from openharness.state.app_state import AppState
from openharness.state.store import AppStateStore


def test_app_state_has_reviews_completed_field():
    """AppState should have reviews_completed with default 0."""
    state = AppState(model="test", permission_mode="default", theme="dark")
    assert state.reviews_completed == 0


def test_app_state_reviews_completed_increments():
    """AppStateStore.set should update reviews_completed."""
    store = AppStateStore(AppState(model="test", permission_mode="default", theme="dark"))
    store.set(reviews_completed=1)
    assert store.get().reviews_completed == 1

    store.set(reviews_completed=2)
    assert store.get().reviews_completed == 2


def test_state_snapshot_includes_reviews_completed():
    """_state_payload should include reviews_completed for frontend."""
    from openharness.ui.protocol import _state_payload

    state = AppState(model="test", permission_mode="default", theme="dark", reviews_completed=3)
    payload = _state_payload(state)
    assert payload["reviews_completed"] == 3
