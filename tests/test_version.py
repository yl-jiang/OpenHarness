from __future__ import annotations

from pathlib import Path

from ohmo import __version__ as ohmo_version
from openharness.version import get_openharness_version


def test_get_openharness_version_prefers_repo_pyproject(monkeypatch):
    monkeypatch.setattr("openharness.version._version_from_pyproject", lambda: "0.1.7")
    monkeypatch.setattr("openharness.version._version_from_metadata", lambda: "0.1.6")

    assert get_openharness_version() == "0.1.7"


def test_repo_versions_are_kept_in_sync():
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")

    assert 'version = "0.1.7"' in text
    assert ohmo_version == "0.1.7"
