"""Tests for project alias management via SoloStore."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from solo.core.models import Project, ProjectAlias
from solo.core.store import SoloStore
from solo.core.utils import _now


def _create_project(store: SoloStore, project_id: str, title: str = "Test Project") -> Project:
    """Helper to create a project in the store."""
    p = Project(id=project_id, title=title, created_at=_now(), updated_at=_now())
    store.create_project(p)
    return p


def _create_alias(
    store: SoloStore,
    project_id: str,
    alias: str,
    source: str = "user",
) -> ProjectAlias:
    """Replicate SoloService.create_project_alias logic."""
    pa = ProjectAlias(
        id=str(uuid4()),
        project_id=project_id,
        alias=alias.strip(),
        source=source,
        created_at=_now(),
    )
    store.create_project_alias(pa)
    return pa


class TestCreateAlias:
    def test_create_alias(self, tmp_path: Path) -> None:
        """Create a project, create an alias, verify it appears in list_project_aliases."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        _create_project(store, "p1")
        pa = _create_alias(store, "p1", "My Alias")

        aliases = store.list_project_aliases("p1")
        assert len(aliases) == 1
        assert aliases[0].id == pa.id
        assert aliases[0].project_id == "p1"
        assert aliases[0].alias == "My Alias"
        assert aliases[0].source == "user"
        assert aliases[0].created_at


class TestDeleteAlias:
    def test_delete_alias(self, tmp_path: Path) -> None:
        """Create a project and alias, delete it, verify it's gone."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        _create_project(store, "p1")
        pa = _create_alias(store, "p1", "To Delete")

        assert len(store.list_project_aliases("p1")) == 1

        deleted = store.delete_project_alias(pa.id)
        assert deleted is True

        assert store.list_project_aliases("p1") == []

    def test_delete_nonexistent_alias(self, tmp_path: Path) -> None:
        """Deleting a non-existent alias returns False."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        assert store.delete_project_alias("nonexistent-id") is False


class TestListAliasesMultiple:
    def test_list_aliases_multiple(self, tmp_path: Path) -> None:
        """Create 3 aliases for one project, verify all 3 are listed."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        _create_project(store, "p1")
        _create_alias(store, "p1", "Alias One")
        _create_alias(store, "p1", "Alias Two")
        _create_alias(store, "p1", "Alias Three")

        aliases = store.list_project_aliases("p1")
        assert len(aliases) == 3

        alias_texts = {a.alias for a in aliases}
        assert alias_texts == {"Alias One", "Alias Two", "Alias Three"}


class TestAliasIsolation:
    def test_alias_isolation(self, tmp_path: Path) -> None:
        """Aliases for different projects are isolated from each other."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        _create_project(store, "p1", "Project One")
        _create_project(store, "p2", "Project Two")

        _create_alias(store, "p1", "P1 Alias A")
        _create_alias(store, "p1", "P1 Alias B")

        _create_alias(store, "p2", "P2 Alias X")

        p1_aliases = store.list_project_aliases("p1")
        p2_aliases = store.list_project_aliases("p2")

        assert len(p1_aliases) == 2
        assert {a.alias for a in p1_aliases} == {"P1 Alias A", "P1 Alias B"}

        assert len(p2_aliases) == 1
        assert p2_aliases[0].alias == "P2 Alias X"

    def test_alias_isolation_empty_project(self, tmp_path: Path) -> None:
        """A project with no aliases returns an empty list."""
        store = SoloStore(tmp_path / ".solo")
        store.initialize()

        _create_project(store, "p1")
        _create_project(store, "p2")

        _create_alias(store, "p1", "Only P1")

        assert len(store.list_project_aliases("p1")) == 1
        assert store.list_project_aliases("p2") == []
