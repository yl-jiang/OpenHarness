from __future__ import annotations

import ast
from pathlib import Path


SCAN_ROOTS = ("src/openharness", "ohmo", "scripts")


def _iter_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for relative_root in SCAN_ROOTS:
        root = repo_root / relative_root
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.py")))
    return files


def _is_module_logger_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "get_logger"
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
        return func.value.id == "logging" and func.attr == "getLogger"
    return False


def _find_logger_assignment_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        if not isinstance(node.value, ast.Call) or not _is_module_logger_call(node.value):
            continue
        target = node.targets[0].id
        if target != "logger":
            violations.append(f"{path}:{node.lineno}:{target}")
        if isinstance(node.value.func, ast.Attribute):
            violations.append(f"{path}:{node.lineno}:stdlib-logging")
    return violations


def test_module_logger_assignments_use_logger_name_and_unified_factory() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in _iter_python_files(repo_root):
        violations.extend(_find_logger_assignment_violations(path))

    assert not violations, (
        "module-level loggers must be assigned to `logger` via get_logger(__name__):\n"
        + "\n".join(violations)
    )
