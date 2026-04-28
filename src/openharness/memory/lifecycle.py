"""Memory provider lifecycle helpers.

Factory functions for creating, initializing, and tearing down the
:class:`MemoryProviderManager` in a runtime session.  Keeping these in a
dedicated module avoids circular imports between ``memory`` and ``ui.runtime``
and makes the logic independently testable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from openharness.memory.providers import (
    BuiltinMemoryProvider,
    MemoryProvider,
    MemoryProviderManager,
)
from openharness.memory.store import MemoryStore
from openharness.utils.log import get_logger

logger = get_logger(__name__)


def setup_memory_provider_manager(
    *,
    curated_dir: str | Path,
    session_id: str,
    extra_providers: Iterable[MemoryProvider] | None = None,
) -> MemoryProviderManager:
    """Create, populate, and initialize a :class:`MemoryProviderManager`.

    The built-in provider is always registered first.  Pass
    ``extra_providers`` to append external / custom providers (at most one
    non-builtin provider is accepted by the manager).

    Returns a manager with all providers initialized.
    """
    curated_dir = Path(curated_dir)
    store = MemoryStore(curated_dir)
    builtin = BuiltinMemoryProvider(curated_dir, store=store)

    manager = MemoryProviderManager()
    manager.add_provider(builtin)
    for provider in extra_providers or ():
        if not provider.is_available():
            logger.info("Skipping unavailable memory provider: %s", provider.name)
            continue
        manager.add_provider(provider)
    manager.initialize_all(session_id)
    return manager


def teardown_memory_provider_manager(
    manager: MemoryProviderManager,
    *,
    messages: list[dict[str, Any]] | None = None,
) -> None:
    """Notify providers of session end and shut them down.

    ``shutdown_all()`` is always called, even if ``on_session_end`` raises.
    """
    try:
        manager.on_session_end(messages or [])
    except Exception as exc:
        logger.warning("on_session_end failed: %s", exc)
    finally:
        manager.shutdown_all()
