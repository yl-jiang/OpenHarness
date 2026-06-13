"""Tests for solo processor _project_context method."""
from __future__ import annotations

import pytest
from pathlib import Path
from solo.core.store import SoloStore
from solo.core.models import Project, Milestone


class TestProjectContext:
    def test_empty_when_no_projects(self, tmp_path: Path) -> None:
        from solo.processor import SoloProcessor
        store = SoloStore(tmp_path / ".solo")
        store.initialize()
        # Create a minimal processor just to call _project_context
        proc = SoloProcessor.__new__(SoloProcessor)
        proc.store = store
        result = proc._project_context()
        assert result == ""

    def test_includes_active_projects(self, tmp_path: Path) -> None:
        from solo.processor import SoloProcessor
        store = SoloStore(tmp_path / ".solo")
        store.initialize()
        p = Project(id="p1", title="My Project", status="active", priority="high")
        store.create_project(p)
        proc = SoloProcessor.__new__(SoloProcessor)
        proc.store = store
        result = proc._project_context()
        assert "My Project" in result
        assert "Active Projects" in result

    def test_shows_completion_and_risk(self, tmp_path: Path) -> None:
        from solo.processor import SoloProcessor
        store = SoloStore(tmp_path / ".solo")
        store.initialize()
        p = Project(id="p1", title="Test", status="active", priority="medium",
                    target_date="2026-01-01")
        store.create_project(p)
        m = Milestone(id="m1", project_id="p1", title="MS1", status="completed")
        store.create_milestone(m)
        proc = SoloProcessor.__new__(SoloProcessor)
        proc.store = store
        result = proc._project_context()
        assert "risk=" in result
        assert "milestones" in result.lower()
