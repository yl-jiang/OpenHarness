"""Tests for the project timeline aggregation logic.

The timeline logic is duplicated in SoloService.get_project_timeline and
WoloService.get_project_timeline.  We test it at the store + logic level
by replicating the aggregation directly, avoiding full service instantiation.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from solo.core.models import Milestone, Project, ProjectSignal, ProjectSnapshot
from solo.core.store import SoloStore


# ---------------------------------------------------------------------------
# Replicate timeline aggregation from SoloService.get_project_timeline
# ---------------------------------------------------------------------------

def build_timeline(store: SoloStore, project_id: str, limit: int = 50) -> list[dict]:
    """Aggregate milestones, signals, snapshots into a unified timeline.

    This is a direct copy of the logic in SoloService.get_project_timeline
    so we can test store + logic without instantiating the full service.
    """
    events: list[dict] = []

    # Milestones
    for m in store.list_milestones(project_id):
        events.append({
            "date": m.completed_at or m.target_date or m.created_at,
            "type": "milestone_completed" if m.status == "completed" else "milestone",
            "title": m.title,
            "detail": "",
        })
        if m.target_date and m.status != "completed":
            events.append({
                "date": m.target_date,
                "type": "milestone_target",
                "title": f"Target: {m.title}",
                "detail": "",
            })

    # Signals
    for s in store.list_project_signals(project_id, limit=20):
        events.append({
            "date": s.created_at,
            "type": f"signal_{s.signal_type}",
            "title": s.summary,
            "detail": s.severity,
        })

    # Snapshots
    for snap in store.list_project_snapshots(project_id, limit=10):
        events.append({
            "date": snap.snapshot_date,
            "type": "snapshot",
            "title": snap.summary or f"Health: {snap.health}",
            "detail": f"{snap.completion_pct or 0}% done, activity_7d={snap.activity_7d}",
        })

    # Project itself
    project = store.get_project(project_id)
    if project:
        events.append({
            "date": project.created_at,
            "type": "project_created",
            "title": f"Project created: {project.title}",
            "detail": "",
        })
        if project.completed_at:
            events.append({
                "date": project.completed_at,
                "type": "project_completed",
                "title": "Project completed",
                "detail": "",
            })

    events.sort(key=lambda e: e.get("date", ""), reverse=True)
    return events[:limit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(tmp_path: Path) -> SoloStore:
    return SoloStore(tmp_path / ".solo")


def _create_project(store: SoloStore, project_id: str = "p1", title: str = "Test", **kw) -> None:
    store.create_project(Project(id=project_id, title=title, **kw))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProjectTimeline:

    def test_timeline_empty_project(self, tmp_path: Path) -> None:
        """A new project with no milestones, signals, or snapshots should
        return just a project_created event."""
        store = _make_store(tmp_path)
        _create_project(store, created_at="2026-01-01T00:00:00")

        timeline = build_timeline(store, "p1")

        assert len(timeline) == 1
        assert timeline[0]["type"] == "project_created"
        assert timeline[0]["date"] == "2026-01-01T00:00:00"
        assert "Test" in timeline[0]["title"]

    def test_timeline_with_milestones(self, tmp_path: Path) -> None:
        """Project with 2 milestones (one pending with target_date, one completed).
        Timeline should include milestone, milestone_target, and milestone_completed events."""
        store = _make_store(tmp_path)
        _create_project(store, created_at="2026-01-01T00:00:00")

        # Pending milestone with a target_date
        store.create_milestone(Milestone(
            id="m1", project_id="p1", title="Design review",
            target_date="2026-03-15", created_at="2026-01-01T00:00:00",
        ))
        # Completed milestone
        store.create_milestone(Milestone(
            id="m2", project_id="p1", title="Kickoff",
            status="completed", completed_at="2026-02-01T00:00:00",
            created_at="2026-01-01T00:00:00",
        ))

        timeline = build_timeline(store, "p1")
        types = [e["type"] for e in timeline]

        # Expect: milestone (pending), milestone_target (pending), milestone_completed, project_created
        assert "milestone" in types
        assert "milestone_target" in types
        assert "milestone_completed" in types
        assert "project_created" in types
        assert len(timeline) == 4

        # The completed milestone should NOT produce a milestone_target event
        target_titles = [e["title"] for e in timeline if e["type"] == "milestone_target"]
        assert len(target_titles) == 1
        assert "Design review" in target_titles[0]

        # The completed milestone event should use completed_at as date
        completed = [e for e in timeline if e["type"] == "milestone_completed"][0]
        assert completed["date"] == "2026-02-01T00:00:00"

    def test_timeline_sort_order(self, tmp_path: Path) -> None:
        """Events at different dates should be sorted descending by date."""
        store = _make_store(tmp_path)
        _create_project(store, created_at="2026-01-01T00:00:00")

        # Milestone completed in March
        store.create_milestone(Milestone(
            id="m1", project_id="p1", title="MS done march",
            status="completed", completed_at="2026-03-10T00:00:00",
            created_at="2026-01-01T00:00:00",
        ))
        # Signal in February
        store.create_project_signal(ProjectSignal(
            id="s1", project_id="p1", signal_type="progress",
            summary="Good progress", severity="info",
            created_at="2026-02-15T00:00:00",
        ))
        # Snapshot in April
        store.create_project_snapshot(ProjectSnapshot(
            id="sn1", project_id="p1", snapshot_date="2026-04-01",
            summary="April snapshot", health="normal",
            completion_pct=60, activity_7d=5,
            created_at="2026-04-01T00:00:00",
        ))

        timeline = build_timeline(store, "p1")
        dates = [e["date"] for e in timeline]

        # Verify descending order
        assert dates == sorted(dates, reverse=True)

        # Specifically: snapshot (Apr) > milestone_completed (Mar) > signal (Feb) > project_created (Jan)
        assert timeline[0]["type"] == "snapshot"
        assert timeline[0]["date"] == "2026-04-01"
        assert timeline[1]["type"] == "milestone_completed"
        assert timeline[1]["date"] == "2026-03-10T00:00:00"
        assert timeline[2]["type"] == "signal_progress"
        assert timeline[2]["date"] == "2026-02-15T00:00:00"
        assert timeline[3]["type"] == "project_created"
        assert timeline[3]["date"] == "2026-01-01T00:00:00"

    def test_timeline_limit(self, tmp_path: Path) -> None:
        """The limit parameter should cap the number of returned events."""
        store = _make_store(tmp_path)
        _create_project(store, created_at="2026-01-01T00:00:00")

        # Create 10 signals at different dates
        for i in range(10):
            store.create_project_signal(ProjectSignal(
                id=f"s{i}", project_id="p1", signal_type="progress",
                summary=f"Signal {i}", severity="info",
                created_at=f"2026-02-{i + 1:02d}T00:00:00",
            ))

        # Without limit: 10 signals + 1 project_created = 11
        full = build_timeline(store, "p1")
        assert len(full) == 11

        # With limit=5: only 5 events returned
        capped = build_timeline(store, "p1", limit=5)
        assert len(capped) == 5

        # The capped result should contain the most recent events (descending sort)
        assert capped[0]["date"] >= capped[-1]["date"]
        # First event should be the latest signal (Feb 10)
        assert capped[0]["date"] == "2026-02-10T00:00:00"
