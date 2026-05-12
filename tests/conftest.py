"""Shared test fixtures."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterator

import pytest
import pytest_asyncio

from openharness.tasks.manager import shutdown_task_manager

_REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="openharness-test-") as tmp_dir:
        path = Path(tmp_dir)
        yield path
        try:
            cwd = Path.cwd()
        except FileNotFoundError:
            os.chdir(_REPO_ROOT)
            return
        if cwd == path or path in cwd.parents:
            os.chdir(_REPO_ROOT)


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
