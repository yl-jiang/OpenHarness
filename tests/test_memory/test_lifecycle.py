"""Tests for MemoryProviderManager wiring in runtime and self-evolution isolation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openharness.memory.providers import (
    BuiltinMemoryProvider,
    MemoryProvider,
    MemoryProviderManager,
)
from openharness.memory.store import MemoryStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class SpyMemoryProvider(MemoryProvider):
    """Fake provider that records lifecycle calls."""

    def __init__(self, name: str = "spy") -> None:
        self._name = name
        self.initialized = False
        self.init_session_id: str | None = None
        self.session_end_calls: list[list[dict[str, Any]]] = []
        self.shutdown_called = False
        self.turn_start_calls: list[tuple[int, str]] = []
        self.synced_turns: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        self.initialized = True
        self.init_session_id = session_id

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        return []

    def on_session_end(self, messages: list[dict[str, Any]]) -> None:
        self.session_end_calls.append(messages)

    def shutdown(self) -> None:
        self.shutdown_called = True

    def on_turn_start(self, turn_number: int, message: str, **kwargs: Any) -> None:
        self.turn_start_calls.append((turn_number, message))

    def sync_turn(self, user_content: str, assistant_content: str, *, session_id: str = "") -> None:
        self.synced_turns.append((user_content, assistant_content))


# ---------------------------------------------------------------------------
# P1a: MemoryProviderManager creation & registration
# ---------------------------------------------------------------------------

def test_create_memory_provider_manager_with_builtin(tmp_path: Path):
    """MemoryProviderManager should be creatable with a BuiltinMemoryProvider."""
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    store = MemoryStore(curated_dir)
    builtin = BuiltinMemoryProvider(curated_dir, store=store)

    manager = MemoryProviderManager()
    manager.add_provider(builtin)
    manager.initialize_all("test-session-123")

    assert len(manager.providers) == 1
    assert manager.providers[0].name == "builtin"


def test_setup_memory_provider_manager_creates_and_initializes(tmp_path: Path):
    """setup_memory_provider_manager should create, register, and initialize the manager."""
    from openharness.memory.lifecycle import setup_memory_provider_manager

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()

    manager = setup_memory_provider_manager(
        curated_dir=curated_dir,
        session_id="sess-42",
    )

    assert isinstance(manager, MemoryProviderManager)
    assert len(manager.providers) == 1
    assert manager.providers[0].name == "builtin"


def test_setup_with_extra_providers(tmp_path: Path):
    """Extra providers should be registered alongside the builtin one."""
    from openharness.memory.lifecycle import setup_memory_provider_manager

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    extra = SpyMemoryProvider("external")

    manager = setup_memory_provider_manager(
        curated_dir=curated_dir,
        session_id="sess-99",
        extra_providers=[extra],
    )

    assert len(manager.providers) == 2
    assert [p.name for p in manager.providers] == ["builtin", "external"]
    assert extra.initialized is True
    assert extra.init_session_id == "sess-99"


def test_setup_skips_unavailable_extra_provider(tmp_path: Path):
    """Unavailable providers should be skipped without error."""
    from openharness.memory.lifecycle import setup_memory_provider_manager

    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    unavailable = SpyMemoryProvider("gone")
    unavailable.is_available = lambda: False  # type: ignore[method-assign]

    manager = setup_memory_provider_manager(
        curated_dir=curated_dir,
        session_id="sess-0",
        extra_providers=[unavailable],
    )

    assert len(manager.providers) == 1
    assert unavailable.initialized is False


def test_builtin_provider_stores_session_id(tmp_path: Path):
    """BuiltinMemoryProvider should preserve the session_id passed at init."""
    curated_dir = tmp_path / "curated"
    curated_dir.mkdir()
    provider = BuiltinMemoryProvider(curated_dir)
    provider.initialize("my-session-id")

    assert provider._session_id == "my-session-id"


# ---------------------------------------------------------------------------
# P1b: on_session_end + shutdown_all
# ---------------------------------------------------------------------------

def test_teardown_memory_provider_manager_calls_session_end_and_shutdown():
    """teardown should call on_session_end then shutdown_all."""
    from openharness.memory.lifecycle import teardown_memory_provider_manager

    spy = SpyMemoryProvider()
    manager = MemoryProviderManager()
    manager.add_provider(spy)

    messages = [{"role": "user", "content": "hello"}]
    teardown_memory_provider_manager(manager, messages=messages)

    assert spy.session_end_calls == [messages]
    assert spy.shutdown_called is True


def test_teardown_calls_shutdown_even_if_session_end_raises():
    """shutdown_all must run even when on_session_end raises."""
    from openharness.memory.lifecycle import teardown_memory_provider_manager

    spy = SpyMemoryProvider()
    manager = MemoryProviderManager()
    manager.add_provider(spy)

    def raise_on_session_end(messages):
        raise RuntimeError("boom")

    spy.on_session_end = raise_on_session_end  # type: ignore[method-assign]
    teardown_memory_provider_manager(manager, messages=[])

    assert spy.shutdown_called is True


# ---------------------------------------------------------------------------
# P1c: Self-evolution isolation
# ---------------------------------------------------------------------------

def test_background_runner_strips_memory_provider_manager():
    """BackgroundSelfEvolutionRunner should not pass memory_provider_manager to review engine."""
    from openharness.evolution.self_evolution import (
        BackgroundSelfEvolutionRunner,
        SelfEvolutionConfig,
    )

    manager = MemoryProviderManager()
    metadata: dict[str, object] = {"memory_provider_manager": manager, "other_key": "value"}

    runner = BackgroundSelfEvolutionRunner(
        api_client=None,  # type: ignore[arg-type]
        tool_registry=None,  # type: ignore[arg-type]
        permission_checker=None,  # type: ignore[arg-type]
        cwd="/tmp",
        model="test",
        system_prompt="test",
        max_tokens=1000,
        config=SelfEvolutionConfig(),
        tool_metadata=metadata,
    )

    # Access the cleaned metadata that would be passed to the review engine
    cleaned = runner._clean_review_metadata()
    assert "memory_provider_manager" not in cleaned
    assert cleaned.get("other_key") == "value"
