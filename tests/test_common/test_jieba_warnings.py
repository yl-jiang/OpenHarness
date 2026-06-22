from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import textwrap


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_import_with_cleared_jieba_cache(module: str) -> subprocess.CompletedProcess[str]:
    script = textwrap.dedent(
        f"""
        import importlib.util
        from pathlib import Path
        import shutil

        spec = importlib.util.find_spec("jieba")
        if spec and spec.origin:
            package_dir = Path(spec.origin).parent
            shutil.rmtree(package_dir / "__pycache__", ignore_errors=True)
            shutil.rmtree(package_dir / "finalseg" / "__pycache__", ignore_errors=True)

        import {module}
        """
    )
    result = subprocess.run(
        [sys.executable, "-Werror::SyntaxWarning", "-c", script],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return result


def test_project_ai_matcher_import_suppresses_jieba_syntaxwarning() -> None:
    result = _run_import_with_cleared_jieba_cache("common.project_ai.matcher")

    assert result.returncode == 0, result.stderr


def test_openharness_search_import_suppresses_jieba_syntaxwarning() -> None:
    result = _run_import_with_cleared_jieba_cache("openharness.search.text")

    assert result.returncode == 0, result.stderr
