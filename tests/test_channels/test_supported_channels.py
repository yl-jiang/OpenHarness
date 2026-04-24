"""Tests for dynamic channel discovery via SUPPORTED_CHANNELS."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import openharness.channels.impl as impl_pkg
from openharness.channels.impl import SUPPORTED_CHANNELS, _discover_channels

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IMPL_DIR = Path(impl_pkg.__file__).parent
_SKIP_MODULES = {"base", "manager", "__init__"}

# All channel *.py files that are expected to define a channel
_CHANNEL_FILES: list[Path] = sorted(
    f for f in _IMPL_DIR.glob("*.py") if f.stem not in _SKIP_MODULES
)

# All known channel names (ground truth derived from the files themselves)
_KNOWN_NAMES = {
    "dingtalk",
    "discord",
    "email",
    "feishu",
    "matrix",
    "mochat",
    "qq",
    "slack",
    "telegram",
    "whatsapp",
}


# ---------------------------------------------------------------------------
# _discover_channels / SUPPORTED_CHANNELS basics
# ---------------------------------------------------------------------------


def test_discover_channels_returns_frozenset():
    result = _discover_channels()
    assert isinstance(result, frozenset)


def test_supported_channels_is_frozenset():
    assert isinstance(SUPPORTED_CHANNELS, frozenset)


def test_discover_channels_non_empty():
    assert len(_discover_channels()) > 0


def test_discover_channels_contains_all_known_channels():
    discovered = _discover_channels()
    missing = _KNOWN_NAMES - discovered
    assert not missing, f"Missing channels: {missing}"


def test_discover_channels_excludes_base():
    assert "base" not in _discover_channels()


def test_supported_channels_matches_discover():
    """Module-level constant must equal what _discover_channels() returns."""
    assert SUPPORTED_CHANNELS == _discover_channels()


def test_supported_channels_exported_from_channels_package():
    """SUPPORTED_CHANNELS must be importable from the top-level channels package."""
    from openharness.channels import SUPPORTED_CHANNELS as sc
    assert sc == SUPPORTED_CHANNELS


def test_discover_channels_idempotent():
    assert _discover_channels() == _discover_channels()


def test_all_channel_names_are_non_empty_strings():
    for name in _discover_channels():
        assert isinstance(name, str) and name.strip(), (
            f"Channel name {name!r} is blank or not a string"
        )


# ---------------------------------------------------------------------------
# Per-file: every channel impl file must declare a ``name`` attribute
# ---------------------------------------------------------------------------


def _ast_names_in_file(path: Path) -> list[str]:
    """Return all channel ``name = '<value>'`` literals found in *path* via AST."""
    names: list[str] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if (
                    isinstance(item, ast.Assign)
                    and len(item.targets) == 1
                    and isinstance(item.targets[0], ast.Name)
                    and item.targets[0].id == "name"
                    and isinstance(item.value, ast.Constant)
                    and isinstance(item.value.value, str)
                ):
                    names.append(item.value.value)
    return names


@pytest.mark.parametrize("channel_file", _CHANNEL_FILES, ids=[f.stem for f in _CHANNEL_FILES])
def test_impl_file_declares_name(channel_file: Path):
    """Each impl file must contain at least one class with ``name = '<channel>'``."""
    names = _ast_names_in_file(channel_file)
    assert names, (
        f"{channel_file.name} does not define a 'name' class attribute. "
        "Add `name = '<channel_id>'` to the channel class so it can be auto-discovered."
    )


@pytest.mark.parametrize("channel_file", _CHANNEL_FILES, ids=[f.stem for f in _CHANNEL_FILES])
def test_impl_file_name_is_non_empty_string(channel_file: Path):
    """The ``name`` value in each impl file must be a non-empty string."""
    for name in _ast_names_in_file(channel_file):
        assert isinstance(name, str) and name.strip(), (
            f"{channel_file.name}: channel name {name!r} is blank"
        )


@pytest.mark.parametrize("channel_file", _CHANNEL_FILES, ids=[f.stem for f in _CHANNEL_FILES])
def test_impl_file_name_present_in_supported_channels(channel_file: Path):
    """Every name declared in an impl file must appear in SUPPORTED_CHANNELS."""
    names = _ast_names_in_file(channel_file)
    for name in names:
        assert name in SUPPORTED_CHANNELS, (
            f"{channel_file.name}: '{name}' not found in SUPPORTED_CHANNELS={sorted(SUPPORTED_CHANNELS)}"
        )
