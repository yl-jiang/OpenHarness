"""Tests for wolo processor _project_context method."""
from __future__ import annotations

from pathlib import Path
from wolo.core.store import WoloStore
from wolo.core.models import Project


class TestWoloProjectContext:
    def test_empty_when_no_projects(self, tmp_path: Path) -> None:
        from wolo.processor import WoloProcessor
        store = WoloStore(tmp_path / ".wolo")
        store.initialize()
        proc = WoloProcessor.__new__(WoloProcessor)
        proc.store = store
        result = proc._project_context()
        assert result == ""

    def test_includes_active_projects(self, tmp_path: Path) -> None:
        from wolo.processor import WoloProcessor
        store = WoloStore(tmp_path / ".wolo")
        store.initialize()
        p = Project(id="p1", title="Work Project", status="active", priority="high")
        store.create_project(p)
        proc = WoloProcessor.__new__(WoloProcessor)
        proc.store = store
        result = proc._project_context()
        assert "Work Project" in result
        assert "Active Projects" in result

    def test_shows_blockers_when_present(self, tmp_path: Path) -> None:
        from wolo.processor import WoloProcessor
        store = WoloStore(tmp_path / ".wolo")
        store.initialize()
        p = Project(id="p1", title="Blocked Proj", status="active", priority="medium")
        store.create_project(p)
        proc = WoloProcessor.__new__(WoloProcessor)
        proc.store = store
        result = proc._project_context()
        # The project has no blockers, so "blockers" should not appear
        assert "Blocked Proj" in result
