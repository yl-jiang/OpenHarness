"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager


@pytest.fixture(autouse=True)
def _isolate_openharness_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / ".openharness"
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(root))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("OPENHARNESS_LOGS_DIR", str(root / "logs"))


@pytest_asyncio.fixture(autouse=True)
async def _reset_background_task_manager():
    yield
    await shutdown_task_manager()
