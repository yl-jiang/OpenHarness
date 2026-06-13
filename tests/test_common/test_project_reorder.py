"""Tests for project link reorder feature."""
from __future__ import annotations

from pathlib import Path

from solo.core.models import Project, ProjectLink
from solo.core.store import SoloStore


class TestProjectLinkReorder:
    """Tests for reorder_project_links and sort order in list_project_links."""

    def test_reorder_basic(self, tmp_path: Path) -> None:
        """Reorder 3 links from [A, B, C] to [C, A, B]."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(id="A", project_id="p1", entity_type="record", entity_id="r1")
        )
        store.create_project_link(
            ProjectLink(id="B", project_id="p1", entity_type="record", entity_id="r2")
        )
        store.create_project_link(
            ProjectLink(id="C", project_id="p1", entity_type="record", entity_id="r3")
        )

        store.reorder_project_links("p1", ["C", "A", "B"])

        links = store.list_project_links(project_id="p1")
        assert [l.id for l in links] == ["C", "A", "B"]

    def test_reorder_preserves_data(self, tmp_path: Path) -> None:
        """Reordering should not change entity_type or entity_id."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(id="l1", project_id="p1", entity_type="record", entity_id="r1")
        )
        store.create_project_link(
            ProjectLink(id="l2", project_id="p1", entity_type="todo", entity_id="t1")
        )

        store.reorder_project_links("p1", ["l2", "l1"])

        links = store.list_project_links(project_id="p1")
        assert links[0].id == "l2"
        assert links[0].entity_type == "todo"
        assert links[0].entity_id == "t1"
        assert links[1].id == "l1"
        assert links[1].entity_type == "record"
        assert links[1].entity_id == "r1"

    def test_reorder_empty_list(self, tmp_path: Path) -> None:
        """Reorder with empty list should not error."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.reorder_project_links("p1", [])

    def test_default_sort_order(self, tmp_path: Path) -> None:
        """Links without reorder should appear in creation order (sort_order=0, ordered by rowid)."""
        store = SoloStore(tmp_path / ".solo")
        store.create_project(Project(id="p1", title="Test"))
        store.create_project_link(
            ProjectLink(id="first", project_id="p1", entity_type="record", entity_id="r1")
        )
        store.create_project_link(
            ProjectLink(id="second", project_id="p1", entity_type="record", entity_id="r2")
        )
        store.create_project_link(
            ProjectLink(id="third", project_id="p1", entity_type="record", entity_id="r3")
        )

        links = store.list_project_links(project_id="p1")
        assert [l.id for l in links] == ["first", "second", "third"]
        assert all(l.sort_order == 0 for l in links)
