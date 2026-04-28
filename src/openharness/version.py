"""Version helpers shared across CLI and TUI entry points."""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
import tomllib

_DIST_NAMES = ("openharness-ai", "openharness")


def _version_from_metadata() -> str | None:
    for dist_name in _DIST_NAMES:
        try:
            version = importlib.metadata.version(dist_name)
        except importlib.metadata.PackageNotFoundError:
            continue
        if version:
            return version
    return None


def _version_from_pyproject() -> str | None:
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.exists():
            continue
        try:
            with pyproject.open("rb") as fh:
                project = tomllib.load(fh).get("project", {})
        except (OSError, tomllib.TOMLDecodeError):
            continue
        version = project.get("version")
        if isinstance(version, str) and version:
            return version
    return None


def get_openharness_version() -> str:
    """Return the package version for both installed and source checkouts."""
    return _version_from_pyproject() or _version_from_metadata() or "unknown"


__all__ = ["get_openharness_version"]
