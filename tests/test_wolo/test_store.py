"""Tests for wolo project management (Phase 0 + Phase 1)."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from wolo.core.models import (
    Milestone,
    Project,
    ProjectAlias,
    ProjectCheckin,
    ProjectLink,
    ProjectSignal,
    ProjectSnapshot,
    ProjectSuggestion,
    WoloTodo,
)
from wolo.core.store import WoloStore


@pytest.fixture()
def store(tmp_path: Path) -> WoloStore:
    """Create a fresh WoloStore in a temp directory."""
    workspace = tmp_path / ".wolo"
    return WoloStore(workspace)


# ---- Empty state ----

def test_list_projects_empty(store: WoloStore) -> None:
    store.initialize()
    assert store.list_projects() == []


def test_list_projects_with_detail_empty(store: WoloStore) -> None:
    store.initialize()
    assert store.list_projects_with_detail() == []


def test_get_project_returns_none(store: WoloStore) -> None:
    store.initialize()
    assert store.get_project("nonexistent") is None


def test_get_project_detail_returns_none(store: WoloStore) -> None:
    store.initialize()
    assert store.get_project_detail("nonexistent") is None


# ---- Schema migration ----

def test_project_tables_exist(store: WoloStore) -> None:
    store.initialize()
    tables = {
        row[0]
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for name in ("projects", "milestones", "project_links", "project_aliases"):
        assert name in tables, f"Missing table: {name}"


def test_project_links_unique_index(store: WoloStore) -> None:
    store.initialize()
    indexes = {
        row[0]
        for row in store._db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_project_links_unique" in indexes


def test_schema_version_is_6(store: WoloStore) -> None:
    store.initialize()
    row = store._db.execute(
        "SELECT value FROM _meta WHERE key='schema_version'"
    ).fetchone()
    assert row is not None
    assert row[0] == "6"


# ---- Project CRUD ----

def test_create_and_get_project(store: WoloStore) -> None:
    store.initialize()
    p = Project(id="p1", title="My Project", description="desc")
    store.create_project(p)
    fetched = store.get_project("p1")
    assert fetched is not None
    assert fetched.id == "p1"
    assert fetched.title == "My Project"
    assert fetched.description == "desc"
    assert fetched.status == "active"
    assert fetched.priority == "medium"


def test_update_project(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Old Title"))
    assert store.update_project("p1", title="New Title", priority="high")
    fetched = store.get_project("p1")
    assert fetched is not None
    assert fetched.title == "New Title"
    assert fetched.priority == "high"


def test_update_nonexistent_project(store: WoloStore) -> None:
    store.initialize()
    assert store.update_project("nope", title="X") is False


def test_delete_project(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="To Delete"))
    assert store.delete_project("p1") is True
    assert store.get_project("p1") is None


def test_delete_nonexistent_project(store: WoloStore) -> None:
    store.initialize()
    assert store.delete_project("nope") is False


def test_delete_project_cascades(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Cascade"))
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M"))
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    store.create_project_alias(
        ProjectAlias(id="pa1", project_id="p1", alias="c")
    )
    assert store.delete_project("p1")
    assert store.list_milestones("p1") == []
    assert store.list_project_links("p1") == []
    assert store.list_project_aliases("p1") == []


def test_complete_project(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    assert store.complete_project("p1")
    p = store.get_project("p1")
    assert p is not None
    assert p.status == "completed"
    assert p.completed_at != ""


def test_complete_already_completed(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.complete_project("p1")
    assert store.complete_project("p1") is False


def test_archive_project(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    assert store.archive_project("p1", "no longer needed")
    p = store.get_project("p1")
    assert p is not None
    assert p.status == "archived"
    assert p.archive_reason == "no longer needed"
    assert p.archived_at != ""


def test_reactivate_project(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.complete_project("p1")
    assert store.reactivate_project("p1")
    p = store.get_project("p1")
    assert p is not None
    assert p.status == "active"
    assert p.completed_at == ""


def test_list_projects_by_status(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="A"))
    store.create_project(Project(id="p2", title="B"))
    store.complete_project("p2")
    active = store.list_projects(status="active")
    assert len(active) == 1
    assert active[0].id == "p1"
    completed = store.list_projects(status="completed")
    assert len(completed) == 1
    assert completed[0].id == "p2"


def test_list_projects_with_limit(store: WoloStore) -> None:
    store.initialize()
    for i in range(5):
        store.create_project(Project(id=f"p{i}", title=f"P{i}"))
    result = store.list_projects(limit=3)
    assert len(result) == 3


# ---- Wolo-specific: stakeholders and success_criteria ----

def test_project_stakeholders(store: WoloStore) -> None:
    store.initialize()
    p = Project(id="p1", title="P", stakeholders="alice,bob,charlie")
    store.create_project(p)
    fetched = store.get_project("p1")
    assert fetched is not None
    assert fetched.stakeholders == "alice,bob,charlie"


def test_project_success_criteria(store: WoloStore) -> None:
    store.initialize()
    p = Project(id="p1", title="P", success_criteria="100 active users")
    store.create_project(p)
    fetched = store.get_project("p1")
    assert fetched is not None
    assert fetched.success_criteria == "100 active users"


def test_project_stakeholders_in_detail(store: WoloStore) -> None:
    store.initialize()
    store.create_project(
        Project(id="p1", title="P", stakeholders="alice", success_criteria="ship it")
    )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["stakeholders"] == "alice"
    assert detail["success_criteria"] == "ship it"


def test_project_update_stakeholders(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.update_project("p1", stakeholders="dave,eve")
    fetched = store.get_project("p1")
    assert fetched is not None
    assert fetched.stakeholders == "dave,eve"


# ---- Milestone CRUD ----

def test_create_and_list_milestones(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M1"))
    store.create_milestone(Milestone(id="m2", project_id="p1", title="M2"))
    ms = store.list_milestones("p1")
    assert len(ms) == 2
    titles = [m.title for m in ms]
    assert "M1" in titles
    assert "M2" in titles


def test_complete_milestone(store: WoloStore) -> None:
    store.initialize()
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M"))
    assert store.complete_milestone("m1")
    # Verify via raw query since we don't have get_milestone
    row = store._db.execute("SELECT status, completed_at FROM milestones WHERE id=?", ("m1",)).fetchone()
    assert row[0] == "completed"
    assert row[1] != ""


def test_complete_already_completed_milestone(store: WoloStore) -> None:
    store.initialize()
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M"))
    store.complete_milestone("m1")
    assert store.complete_milestone("m1") is False


def test_update_milestone(store: WoloStore) -> None:
    store.initialize()
    store.create_milestone(Milestone(id="m1", project_id="p1", title="Old"))
    assert store.update_milestone("m1", title="New")
    row = store._db.execute("SELECT title FROM milestones WHERE id=?", ("m1",)).fetchone()
    assert row[0] == "New"


def test_update_nonexistent_milestone(store: WoloStore) -> None:
    store.initialize()
    assert store.update_milestone("nope", title="X") is False


def test_delete_milestone(store: WoloStore) -> None:
    store.initialize()
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M"))
    assert store.delete_milestone("m1")
    assert store.list_milestones("p1") == []


def test_delete_nonexistent_milestone(store: WoloStore) -> None:
    store.initialize()
    assert store.delete_milestone("nope") is False


# ---- ProjectLink CRUD ----

def test_create_and_list_project_links(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    store.create_project_link(
        ProjectLink(id="pl2", project_id="p1", entity_type="todo", entity_id="t1")
    )
    links = store.list_project_links("p1")
    assert len(links) == 2


def test_list_project_links_by_entity_type(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    store.create_project_link(
        ProjectLink(id="pl2", project_id="p1", entity_type="todo", entity_id="t1")
    )
    records = store.list_project_links("p1", entity_type="record")
    assert len(records) == 1
    assert records[0].entity_type == "record"


def test_project_link_unique_constraint(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    with pytest.raises(Exception):
        store.create_project_link(
            ProjectLink(id="pl2", project_id="p1", entity_type="record", entity_id="r1")
        )


def test_accept_project_link(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(
            id="pl1", project_id="p1", entity_type="record",
            entity_id="r1", status="pending",
        )
    )
    assert store.accept_project_link("pl1")
    row = store._db.execute("SELECT status FROM project_links WHERE id=?", ("pl1",)).fetchone()
    assert row[0] == "active"


def test_accept_non_pending_link(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1", status="active")
    )
    assert store.accept_project_link("pl1") is False


def test_reject_project_link(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(
            id="pl1", project_id="p1", entity_type="record",
            entity_id="r1", status="pending",
        )
    )
    assert store.reject_project_link("pl1")
    row = store._db.execute("SELECT status FROM project_links WHERE id=?", ("pl1",)).fetchone()
    assert row[0] == "rejected"


def test_update_project_link(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    assert store.update_project_link("pl1", confidence="0.95")
    row = store._db.execute("SELECT confidence FROM project_links WHERE id=?", ("pl1",)).fetchone()
    assert row[0] == "0.95"


def test_update_nonexistent_project_link(store: WoloStore) -> None:
    store.initialize()
    assert store.update_project_link("nope", confidence="1") is False


def test_delete_project_link(store: WoloStore) -> None:
    store.initialize()
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    assert store.delete_project_link("pl1")
    assert store.list_project_links("p1") == []


# ---- ProjectAlias CRUD ----

def test_create_and_list_aliases(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_project_alias(ProjectAlias(id="pa1", project_id="p1", alias="proj"))
    store.create_project_alias(ProjectAlias(id="pa2", project_id="p1", alias="p"))
    aliases = store.list_project_aliases("p1")
    assert len(aliases) == 2


def test_delete_alias(store: WoloStore) -> None:
    store.initialize()
    store.create_project_alias(ProjectAlias(id="pa1", project_id="p1", alias="x"))
    assert store.delete_project_alias("pa1")
    assert store.list_project_aliases("p1") == []


def test_delete_nonexistent_alias(store: WoloStore) -> None:
    store.initialize()
    assert store.delete_project_alias("nope") is False


# ---- Completion calculation ----

def test_completion_milestone_based(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M1", status="completed"))
    store.create_milestone(Milestone(id="m2", project_id="p1", title="M2", status="pending"))
    store.create_milestone(Milestone(id="m3", project_id="p1", title="M3", status="pending"))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["milestone_count"] == 3
    assert detail["completed_milestone_count"] == 1
    assert detail["completion_source"] == "milestones"
    assert detail["completion_pct"] == 33


def test_completion_todo_based(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    # Create todos and link them
    for i in range(4):
        todo = WoloTodo(
            id=f"t{i}", record_id="r1", title=f"Todo {i}",
            status="done" if i == 0 else "pending",
            created_at="2026-01-01T00:00:00",
        )
        store.add_todo(todo)
        store.create_project_link(
            ProjectLink(
                id=f"pl{i}", project_id="p1",
                entity_type="todo", entity_id=f"t{i}",
            )
        )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["milestone_count"] == 0
    assert detail["linked_todo_count"] == 4
    assert detail["completed_linked_todo_count"] == 1
    assert detail["completion_source"] == "todos"
    assert detail["completion_pct"] == 25


def test_completion_none_no_data(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["completion_pct"] is None
    assert detail["completion_source"] == "none"


def test_completion_milestones_preferred_over_todos(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M1", status="completed"))
    store.add_todo(
        WoloTodo(id="t1", record_id="r1", title="T1", status="pending", created_at="2026-01-01T00:00:00")
    )
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="todo", entity_id="t1")
    )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["completion_source"] == "milestones"
    assert detail["completion_pct"] == 100


# ---- Risk calculation ----

def test_risk_at_risk_overdue(store: WoloStore) -> None:
    store.initialize()
    past = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    store.create_project(Project(id="p1", title="P", target_date=past))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["risk_status"] == "at_risk"


def test_risk_attention_due_soon(store: WoloStore) -> None:
    store.initialize()
    soon = (datetime.now(timezone.utc) + timedelta(days=3)).date().isoformat()
    store.create_project(Project(id="p1", title="P", target_date=soon))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["risk_status"] == "attention"


def test_risk_normal_on_track(store: WoloStore) -> None:
    store.initialize()
    far = (datetime.now(timezone.utc) + timedelta(days=60)).date().isoformat()
    store.create_project(Project(id="p1", title="P", target_date=far))
    # Add recent activity
    store.create_project_link(
        ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["risk_status"] == "normal"


def test_risk_attention_no_activity(store: WoloStore) -> None:
    store.initialize()
    far = (datetime.now(timezone.utc) + timedelta(days=60)).date().isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
    store.create_project(
        Project(id="p1", title="P", target_date=far, created_at=old)
    )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["risk_status"] == "attention"


def test_risk_completed_project_is_normal(store: WoloStore) -> None:
    store.initialize()
    past = (datetime.now(timezone.utc) - timedelta(days=10)).date().isoformat()
    store.create_project(Project(id="p1", title="P", target_date=past, status="completed"))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["risk_status"] == "normal"


# ---- list_projects_with_detail ----

def test_list_projects_with_detail(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="A"))
    store.create_project(Project(id="p2", title="B"))
    store.complete_project("p2")
    result = store.list_projects_with_detail(status="active")
    assert len(result) == 1
    assert result[0]["id"] == "p1"


def test_list_projects_with_detail_pagination(store: WoloStore) -> None:
    store.initialize()
    for i in range(5):
        store.create_project(Project(id=f"p{i}", title=f"P{i}"))
    page = store.list_projects_with_detail(limit=2, offset=1)
    assert len(page) == 2


# ---- Activity counts ----

def test_activity_counts(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    now = datetime.now(timezone.utc).isoformat()
    store.create_project_link(
        ProjectLink(
            id="pl1", project_id="p1", entity_type="record",
            entity_id="r1", created_at=now,
        )
    )
    old = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    store.create_project_link(
        ProjectLink(
            id="pl2", project_id="p1", entity_type="record",
            entity_id="r2", created_at=old,
        )
    )
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert detail["activity_7d"] == 1
    assert detail["activity_30d"] == 2


# ---- Model serialization ----

def test_project_to_dict_and_json() -> None:
    p = Project(id="p1", title="T", stakeholders="alice", success_criteria="done")
    d = p.to_dict()
    assert d["id"] == "p1"
    assert d["stakeholders"] == "alice"
    assert d["success_criteria"] == "done"
    import json
    parsed = json.loads(p.to_json())
    assert parsed == d


def test_milestone_to_dict() -> None:
    m = Milestone(id="m1", project_id="p1", title="M")
    d = m.to_dict()
    assert d["id"] == "m1"
    assert d["project_id"] == "p1"


def test_project_link_to_dict() -> None:
    pl = ProjectLink(id="pl1", project_id="p1", entity_type="record", entity_id="r1")
    d = pl.to_dict()
    assert d["entity_type"] == "record"
    assert d["source"] == "user"


def test_project_alias_to_dict() -> None:
    pa = ProjectAlias(id="pa1", project_id="p1", alias="x")
    d = pa.to_dict()
    assert d["alias"] == "x"
    assert d["source"] == "user"


# ---- status="all" filter ----

def test_list_projects_status_all(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Active", status="active"))
    store.create_project(Project(id="p2", title="Done", status="completed"))
    store.create_project(Project(id="p3", title="Old", status="archived"))
    result = store.list_projects(status="all")
    assert len(result) == 3


# ---- resolve_entity_summary ----

def test_resolve_entity_summary(store: WoloStore) -> None:
    store.initialize()
    # Record
    store._db.execute(
        "INSERT INTO records (id, entry_id, date, raw_content, corrected_content, summary, tags, emotion) "
        "VALUES ('r1', 'e1', '2026-01-01', 'raw', 'corrected', 'A summary', '', '')"
    )
    store._db.commit()
    assert store.resolve_entity_summary("record", "r1") == "A summary"
    # Todo
    store.add_todo(WoloTodo(id="t1", record_id="r1", title="Fix bug", created_at="2026-01-01T00:00:00"))
    assert store.resolve_entity_summary("todo", "t1") == "Fix bug"
    # Decision
    store._db.execute(
        "INSERT INTO decisions (id, record_id, title) VALUES ('d1', 'r1', 'Use React')"
    )
    store._db.commit()
    assert store.resolve_entity_summary("decision", "d1") == "Use React"
    # Highlight with kind
    store._db.execute(
        "INSERT INTO highlights (id, record_id, kind, title) VALUES ('h1', 'r1', 'blocker', 'API blocked')"
    )
    store._db.commit()
    assert store.resolve_entity_summary("highlight", "h1") == "API blocked [blocker]"
    # Nonexistent
    assert store.resolve_entity_summary("record", "nonexistent") == ""


# ---- completion_pct is integer ----

def test_completion_pct_is_integer(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="P"))
    store.create_milestone(Milestone(id="m1", project_id="p1", title="M1", status="completed"))
    store.create_milestone(Milestone(id="m2", project_id="p1", title="M2", status="pending"))
    store.create_milestone(Milestone(id="m3", project_id="p1", title="M3", status="pending"))
    detail = store.get_project_detail("p1")
    assert detail is not None
    assert isinstance(detail["completion_pct"], int)


# ---- ProjectSuggestion CRUD (Phase 1) ----

def test_create_and_list_suggestion(store: WoloStore) -> None:
    store.initialize()
    sid = str(uuid4())
    suggestion = ProjectSuggestion(
        id=sid,
        suggestion_type="link_entity",
        project_id="p1",
        title="Test suggestion",
        rationale="Record mentions project",
        confidence=0.75,
    )
    store.create_project_suggestion(suggestion)
    results = store.list_project_suggestions()
    assert len(results) == 1
    assert results[0].id == sid
    assert results[0].suggestion_type == "link_entity"
    assert results[0].title == "Test suggestion"
    assert results[0].confidence == 0.75
    assert results[0].status == "pending"


def test_accept_suggestion(store: WoloStore) -> None:
    store.initialize()
    sid = str(uuid4())
    store.create_project_suggestion(
        ProjectSuggestion(id=sid, suggestion_type="link_entity", title="Suggest")
    )
    assert store.accept_project_suggestion(sid)
    results = store.list_project_suggestions()
    assert len(results) == 1
    assert results[0].status == "accepted"


def test_reject_suggestion(store: WoloStore) -> None:
    store.initialize()
    sid = str(uuid4())
    store.create_project_suggestion(
        ProjectSuggestion(id=sid, suggestion_type="link_entity", title="Suggest")
    )
    assert store.reject_project_suggestion(sid)
    results = store.list_project_suggestions()
    assert len(results) == 1
    assert results[0].status == "rejected"


def test_snooze_suggestion(store: WoloStore) -> None:
    store.initialize()
    sid = str(uuid4())
    store.create_project_suggestion(
        ProjectSuggestion(id=sid, suggestion_type="link_entity", title="Suggest")
    )
    assert store.snooze_project_suggestion(sid)
    results = store.list_project_suggestions()
    assert len(results) == 1
    assert results[0].status == "snoozed"


def test_filter_suggestions_by_status(store: WoloStore) -> None:
    store.initialize()
    sid1 = str(uuid4())
    sid2 = str(uuid4())
    store.create_project_suggestion(
        ProjectSuggestion(id=sid1, suggestion_type="link_entity", title="Pending one")
    )
    store.create_project_suggestion(
        ProjectSuggestion(id=sid2, suggestion_type="link_entity", title="Accepted one")
    )
    store.accept_project_suggestion(sid2)

    pending = store.list_project_suggestions(status="pending")
    assert len(pending) == 1
    assert pending[0].id == sid1

    accepted = store.list_project_suggestions(status="accepted")
    assert len(accepted) == 1
    assert accepted[0].id == sid2


# ---- Phase 3: Signals, Snapshots, Checkins CRUD ----


def test_create_and_list_signals(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Test", status="active"))
    sig = ProjectSignal(
        id="s1", project_id="p1", signal_type="risk",
        summary="Overdue target", severity="critical",
        created_at="2026-06-12T00:00:00+00:00",
    )
    store.create_project_signal(sig)
    signals = store.list_project_signals("p1")
    assert len(signals) == 1
    assert signals[0].signal_type == "risk"


def test_delete_signal(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Test", status="active"))
    sig = ProjectSignal(id="s1", project_id="p1", signal_type="progress", summary="OK", created_at="")
    store.create_project_signal(sig)
    assert store.delete_project_signal("s1")
    assert store.list_project_signals("p1") == []


def test_create_and_list_snapshots(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Test", status="active"))
    snap = ProjectSnapshot(
        id="sn1", project_id="p1", snapshot_date="2026-06-12",
        summary="On track", health="normal", completion_pct=50,
        activity_7d=3, next_action="Continue work",
        created_at="2026-06-12T00:00:00+00:00",
    )
    store.create_project_snapshot(snap)
    snapshots = store.list_project_snapshots("p1")
    assert len(snapshots) == 1
    latest = store.get_latest_project_snapshot("p1")
    assert latest is not None
    assert latest.health == "normal"


def test_create_and_list_checkins(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Test", status="active"))
    ci = ProjectCheckin(
        id="c1", project_id="p1", question="Any progress?",
        status="sent", created_at="2026-06-12T00:00:00+00:00",
    )
    store.create_project_checkin(ci)
    checkins = store.list_project_checkins("p1")
    assert len(checkins) == 1
    assert checkins[0].question == "Any progress?"


def test_update_checkin(store: WoloStore) -> None:
    store.initialize()
    store.create_project(Project(id="p1", title="Test", status="active"))
    ci = ProjectCheckin(
        id="c1", project_id="p1", question="Status?",
        status="sent", created_at="2026-06-12T00:00:00+00:00",
    )
    store.create_project_checkin(ci)
    assert store.update_project_checkin("c1", status="answered", responded_at="2026-06-12T12:00:00+00:00")
    checkins = store.list_project_checkins("p1", status="answered")
    assert len(checkins) == 1
