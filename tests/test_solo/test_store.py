"""Tests for solo store project management features."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from solo.core.models import Milestone, Project, ProjectAlias, ProjectLink, SoloTodo
from solo.core.store import SoloStore


class TestProjectStoreEmptyState:
    """Phase 0: Baseline tests for empty state."""

    def test_list_projects_empty(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.list_projects() == []

    def test_list_milestones_empty(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.list_milestones("nonexistent") == []

    def test_schema_migration_creates_project_tables(self, tmp_path: Path) -> None:
        """Old DB should get new tables after store init."""
        store = SoloStore(tmp_path / ".solo")
        _ = store._db  # trigger init
        tables = {
            row[0]
            for row in store._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "projects" in tables
        assert "milestones" in tables
        assert "project_links" in tables
        assert "project_aliases" in tables


class TestProjectCRUD:
    """Phase 0+1: Project lifecycle tests."""

    def test_create_and_get_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        p = Project(id="p1", title="Test Project", created_at="2026-01-01T00:00:00")
        store.create_project(p)
        got = store.get_project("p1")
        assert got is not None
        assert got.title == "Test Project"
        assert got.status == "active"

    def test_update_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Old"))
        assert store.update_project("p1", title="New", description="desc")
        got = store.get_project("p1")
        assert got.title == "New"
        assert got.description == "desc"

    def test_complete_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        assert store.complete_project("p1")
        got = store.get_project("p1")
        assert got.status == "completed"
        assert got.completed_at != ""

    def test_archive_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        assert store.archive_project("p1", reason="no longer needed")
        got = store.get_project("p1")
        assert got.status == "archived"
        assert got.archive_reason == "no longer needed"

    def test_reactivate_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.complete_project("p1")
        assert store.reactivate_project("p1")
        got = store.get_project("p1")
        assert got.status == "active"
        assert got.completed_at == ""

    def test_delete_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        assert store.delete_project("p1")
        assert store.get_project("p1") is None

    def test_delete_project_preserves_source_entities(
        self, tmp_path: Path
    ) -> None:
        """Deleting project should NOT delete linked records/todos."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(
                id="pl1", project_id="p1", entity_type="record", entity_id="r1"
            )
        )
        store.delete_project("p1")
        # Link should be gone (cascade)
        assert store.list_project_links(project_id="p1") == []

    def test_list_projects_filter_by_status(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Active", updated_at="2026-01-01"))
        store.create_project(Project(id="p2", title="Done", updated_at="2026-01-02"))
        store.complete_project("p2")
        active = store.list_projects(status="active")
        assert len(active) == 1
        assert active[0].id == "p1"

    def test_get_nonexistent_project(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.get_project("nonexistent") is None


class TestMilestoneCRUD:
    def test_create_and_list_milestones(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_milestone(Milestone(id="m1", project_id="p1", title="M1"))
        store.create_milestone(Milestone(id="m2", project_id="p1", title="M2"))
        ms = store.list_milestones("p1")
        assert len(ms) == 2

    def test_complete_milestone(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_milestone(Milestone(id="m1", project_id="p1", title="M1"))
        assert store.complete_milestone("m1")
        ms = store.list_milestones("p1")
        assert ms[0].status == "completed"
        assert ms[0].completed_at != ""

    def test_delete_milestone(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_milestone(Milestone(id="m1", project_id="p1", title="M1"))
        assert store.delete_milestone("m1")
        assert store.list_milestones("p1") == []


class TestProjectLinkCRUD:
    def test_create_and_list_links(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(
                id="pl1", project_id="p1", entity_type="record", entity_id="r1"
            )
        )
        links = store.list_project_links(project_id="p1")
        assert len(links) == 1

    def test_accept_reject_link(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(
                id="pl1",
                project_id="p1",
                entity_type="record",
                entity_id="r1",
                status="pending",
            )
        )
        store.accept_project_link("pl1")
        link = store.list_project_links(project_id="p1")[0]
        assert link.status == "active"

        store.reject_project_link("pl1")
        link = store.list_project_links(project_id="p1")[0]
        assert link.status == "rejected"

    def test_delete_link(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(
                id="pl1", project_id="p1", entity_type="record", entity_id="r1"
            )
        )
        assert store.delete_project_link("pl1")
        assert store.list_project_links(project_id="p1") == []

    def test_unique_constraint(self, tmp_path: Path) -> None:
        """Same project + entity_type + entity_id should fail."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(
                id="pl1", project_id="p1", entity_type="record", entity_id="r1"
            )
        )
        with pytest.raises(Exception):
            store.create_project_link(
                ProjectLink(
                    id="pl2", project_id="p1", entity_type="record", entity_id="r1"
                )
            )


class TestProjectAliasCRUD:
    def test_create_and_list_aliases(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_alias(
            ProjectAlias(id="pa1", project_id="p1", alias="test-alias")
        )
        aliases = store.list_project_aliases("p1")
        assert len(aliases) == 1
        assert aliases[0].alias == "test-alias"

    def test_delete_alias(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_alias(
            ProjectAlias(id="pa1", project_id="p1", alias="test-alias")
        )
        assert store.delete_project_alias("pa1")
        assert store.list_project_aliases("p1") == []


class TestCompletionCalculation:
    """Phase 0: Completion percentage calculation tests."""

    def test_completion_with_milestones(self, tmp_path: Path) -> None:
        """2 milestones, 1 completed => 50%."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_milestone(Milestone(id="m1", project_id="p1", title="M1"))
        store.create_milestone(Milestone(id="m2", project_id="p1", title="M2"))
        store.complete_milestone("m1")
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["completion_pct"] == 50
        assert detail["completion_source"] == "milestones"

    def test_completion_with_todos_only(self, tmp_path: Path) -> None:
        """No milestones, 4 linked todos, 1 completed => 25%."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        # Link 4 todos
        for i in range(4):
            store.create_project_link(
                ProjectLink(
                    id=f"pl{i}",
                    project_id="p1",
                    entity_type="todo",
                    entity_id=f"t{i}",
                )
            )
        # Add actual todos in the DB for completion calculation
        for i in range(4):
            store.add_todo(
                SoloTodo(
                    id=f"t{i}",
                    record_id="r1",
                    title=f"Todo {i}",
                    status="done" if i == 0 else "pending",
                    created_at="2026-01-01T00:00:00",
                )
            )
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["completion_pct"] == 25
        assert detail["completion_source"] == "todos"

    def test_completion_none_when_no_source(self, tmp_path: Path) -> None:
        """No milestones and no linked todos => None."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["completion_pct"] is None
        assert detail["completion_source"] == "none"

    def test_target_date_does_not_affect_completion(self, tmp_path: Path) -> None:
        """Expired target_date changes risk, not completion."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(
            Project(id="p1", title="Test", target_date="2020-01-01")
        )
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["completion_pct"] is None
        assert detail["risk_status"] == "at_risk"


class TestRiskCalculation:
    def test_risk_at_risk_when_target_date_passed(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(
            Project(id="p1", title="Test", target_date="2020-01-01")
        )
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["risk_status"] == "at_risk"

    def test_risk_attention_when_near_deadline(self, tmp_path: Path) -> None:
        near_date = (
            datetime.now(timezone.utc) + timedelta(days=3)
        ).strftime("%Y-%m-%d")
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test", target_date=near_date))
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["risk_status"] == "attention"

    def test_risk_normal(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))  # no target_date
        detail = store.get_project_detail("p1")
        assert detail is not None
        assert detail["risk_status"] == "normal"
