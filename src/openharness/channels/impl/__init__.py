"""Chat channel implementations."""

import ast
from pathlib import Path

from openharness.channels.impl.base import BaseChannel
from openharness.channels.impl.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager", "SUPPORTED_CHANNELS"]

_SKIP_MODULES = {"base", "manager", "__init__"}


def _discover_channels() -> frozenset[str]:
    """Scan impl/*.py via AST and collect every channel ``name`` class attribute."""
    impl_dir = Path(__file__).parent
    names: set[str] = set()
    for py_file in impl_dir.glob("*.py"):
        if py_file.stem in _SKIP_MODULES:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
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
                            names.add(item.value.value)
        except Exception:
            pass
    return frozenset(names)


SUPPORTED_CHANNELS: frozenset[str] = _discover_channels()
