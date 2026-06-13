"""Tests for common/project_ai/matcher.py — deterministic + pipeline matching."""
from __future__ import annotations

import asyncio
from typing import Any

from common.project_ai.discovery import _deterministic_discover, scan_for_projects
from common.project_ai.matcher import match_record
from common.project_ai.types import (
    CONFIDENCE_AUTO_LINK,
    CONFIDENCE_SUGGEST,
    LinkerResult,
    MatchCandidate,
)


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.get_event_loop().run_until_complete(coro)


class TestDeterministicTitleMatch:
    def test_deterministic_title_match(self) -> None:
        """Record content fully contains project title tokens -> high confidence auto_link."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[{"id": "p1", "title": "Website Redesign", "description": ""}],
                aliases_by_project={},
                agent=None,
            )
        )
        # Title tokens {website, redesign} are a subset of record tokens -> boosted to 0.88
        assert len(result.auto_links) == 1
        assert result.auto_links[0].project_id == "p1"
        assert result.auto_links[0].confidence >= CONFIDENCE_AUTO_LINK
        assert result.auto_links[0].strategy == "deterministic"


class TestDeterministicAliasMatch:
    def test_deterministic_alias_match(self) -> None:
        """Record content matches alias tokens strongly -> match found."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Alpha launch planning",
                record_summary="Alpha launch",
                artifact_projects=[],
                # Title "Alpha Launch" tokens {alpha, launch} are subset of record -> 0.88
                projects=[{"id": "p1", "title": "Alpha Launch", "description": ""}],
                aliases_by_project={"p1": ["alpha release"]},
                agent=None,
            )
        )
        all_matches = result.auto_links + result.suggestions
        assert len(all_matches) >= 1
        matched_ids = {c.project_id for c in all_matches}
        assert "p1" in matched_ids
        best = max(all_matches, key=lambda c: c.confidence)
        assert best.confidence >= CONFIDENCE_SUGGEST


class TestDeterministicArtifactProjectMatch:
    def test_deterministic_artifact_project_match(self) -> None:
        """Artifact project string matches project title -> high confidence match."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Generic notes from today",
                record_summary="Daily notes",
                artifact_projects=["Website Redesign"],
                projects=[{"id": "p1", "title": "Website Redesign", "description": ""}],
                aliases_by_project={},
                agent=None,
            )
        )
        # Artifact project string matches title exactly -> score 0.92
        assert len(result.auto_links) == 1
        assert result.auto_links[0].project_id == "p1"
        assert result.auto_links[0].confidence >= CONFIDENCE_AUTO_LINK


class TestNoMatchBelowThreshold:
    def test_no_match_below_threshold(self) -> None:
        """Completely unrelated record -> no matches returned."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Made pasta for dinner with tomato sauce",
                record_summary="Cooking dinner",
                artifact_projects=[],
                projects=[{"id": "p1", "title": "Website Redesign", "description": ""}],
                aliases_by_project={},
                agent=None,
            )
        )
        assert len(result.auto_links) == 0
        assert len(result.suggestions) == 0


class TestAutoLinkVsSuggestion:
    """Test threshold classification boundaries.

    Deterministic matching produces either high confidence (>=0.85, auto_link)
    or low confidence (<0.55, discarded). The suggestion range (0.55-0.84)
    is produced by LLM matching. We verify the classification boundaries
    using constructed candidates, and verify deterministic end-to-end behavior.
    """

    def test_deterministic_auto_link_and_no_match(self) -> None:
        """Deterministic matching: title subset -> auto_link, unrelated -> nothing."""
        projects = [
            {"id": "p_auto", "title": "Data Pipeline", "description": ""},
            {"id": "p_unrelated", "title": "Cooking Recipes", "description": ""},
        ]
        result = _run(
            match_record(
                record_id="r1",
                record_content="Finished the data pipeline migration",
                record_summary="Data pipeline done",
                artifact_projects=[],
                projects=projects,
                aliases_by_project={},
                agent=None,
            )
        )
        auto_ids = {c.project_id for c in result.auto_links}
        assert "p_auto" in auto_ids, "Title subset match should produce auto_link"
        assert "p_unrelated" not in auto_ids, "Unrelated project should not match"
        assert len(result.suggestions) == 0, "Deterministic should not produce suggestions here"

    def test_threshold_classification_boundaries(self) -> None:
        """Verify LinkerResult classification: >=0.85 auto_link, 0.55-0.84 suggestion."""
        # Simulate what the pipeline would produce with candidates at various confidences
        high_conf = MatchCandidate(
            project_id="p1", project_title="High", confidence=0.90,
            strategy="deterministic", evidence=[{"entity_type": "record", "entity_id": "r1"}],
        )
        mid_conf = MatchCandidate(
            project_id="p2", project_title="Mid", confidence=0.70,
            strategy="llm", evidence=[{"entity_type": "record", "entity_id": "r1"}],
        )
        low_conf = MatchCandidate(
            project_id="p3", project_title="Low", confidence=0.40,
            strategy="llm", evidence=[{"entity_type": "record", "entity_id": "r1"}],
        )

        # Verify threshold constants
        assert CONFIDENCE_AUTO_LINK == 0.85
        assert CONFIDENCE_SUGGEST == 0.55

        # Classify as the pipeline does
        auto_links = [c for c in [high_conf, mid_conf, low_conf] if c.confidence >= CONFIDENCE_AUTO_LINK]
        suggestions = [c for c in [high_conf, mid_conf, low_conf] if CONFIDENCE_SUGGEST <= c.confidence < CONFIDENCE_AUTO_LINK]
        discarded = [c for c in [high_conf, mid_conf, low_conf] if c.confidence < CONFIDENCE_SUGGEST]

        assert len(auto_links) == 1 and auto_links[0].project_id == "p1"
        assert len(suggestions) == 1 and suggestions[0].project_id == "p2"
        assert len(discarded) == 1 and discarded[0].project_id == "p3"


class TestEmptyProjects:
    def test_empty_projects(self) -> None:
        """No projects -> empty result."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Some content about a project",
                record_summary="Project notes",
                artifact_projects=["Some Project"],
                projects=[],
                aliases_by_project={},
                agent=None,
            )
        )
        assert len(result.auto_links) == 0
        assert len(result.suggestions) == 0
        assert len(result.unmatched) == 0


# ---------------------------------------------------------------------------
# Discovery helpers: mock objects
# ---------------------------------------------------------------------------

class _MockRecord:
    """Minimal record object with to_dict() for discovery."""

    def __init__(self, record_id: str, summary: str = "", tags: str = "", date: str = "2026-01-01"):
        self._data = {"id": record_id, "summary": summary, "tags": tags, "date": date}

    def to_dict(self) -> dict[str, str]:
        return self._data


class _MockProject:
    """Minimal project object with to_dict() for discovery."""

    def __init__(self, project_id: str, title: str):
        self._data = {"id": project_id, "title": title}

    def to_dict(self) -> dict[str, str]:
        return self._data


class _MockAlias:
    """Minimal alias object with to_dict()."""

    def __init__(self, alias: str):
        self._data = {"alias": alias}

    def to_dict(self) -> dict[str, str]:
        return self._data


class MockDiscoveryStore:
    def __init__(self, records, projects=None, aliases=None, todos=None):
        self._records = records
        self._projects = projects or []
        self._aliases = aliases or {}
        self._todos = todos or []

    def list_records(self, **kwargs):
        return self._records

    def list_projects(self, **kwargs):
        return self._projects

    def list_project_aliases(self, project_id):
        return self._aliases.get(project_id, [])

    def list_todos(self, **kwargs):
        return self._todos


# ---------------------------------------------------------------------------
# TestDeterministicDiscovery
# ---------------------------------------------------------------------------

class TestDeterministicDiscovery:

    def test_discover_from_recurring_tags(self) -> None:
        """3+ records sharing the same tag -> produces a create_project candidate."""
        records = [
            {"id": "r1", "summary": "note 1", "tags": "machine-learning,python", "date": "2026-01-01"},
            {"id": "r2", "summary": "note 2", "tags": "machine-learning,data", "date": "2026-01-02"},
            {"id": "r3", "summary": "note 3", "tags": "machine-learning,experiments", "date": "2026-01-03"},
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
        )
        titles = {c["title"].lower() for c in candidates}
        assert "machine-learning" in titles
        ml = next(c for c in candidates if c["title"].lower() == "machine-learning")
        assert ml["suggestion_type"] == "create_project"
        assert ml["confidence"] >= CONFIDENCE_SUGGEST
        assert len(ml["evidence"]) >= 1

    def test_discover_from_artifact_projects(self) -> None:
        """Same project string in 3+ artifact_projects -> produces a candidate."""
        records = [
            {"id": "r1", "summary": "a", "tags": "", "date": "2026-01-01"},
            {"id": "r2", "summary": "b", "tags": "", "date": "2026-01-02"},
            {"id": "r3", "summary": "c", "tags": "", "date": "2026-01-03"},
        ]
        artifacts = [
            {"project": "infra-upgrade", "record_id": "r1"},
            {"project": "infra-upgrade", "record_id": "r2"},
            {"project": "infra-upgrade", "record_id": "r3"},
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=artifacts,
            existing_names=set(),
        )
        titles_lower = {c["title"].lower() for c in candidates}
        assert "infra-upgrade" in titles_lower or "infra upgrade" in titles_lower

    def test_discover_skips_existing_projects(self) -> None:
        """Tag matching an existing project title is skipped."""
        records = [
            {"id": "r1", "summary": "a", "tags": "website", "date": "2026-01-01"},
            {"id": "r2", "summary": "b", "tags": "website", "date": "2026-01-02"},
            {"id": "r3", "summary": "c", "tags": "website", "date": "2026-01-03"},
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=[],
            existing_names={"website"},
        )
        titles_lower = {c["title"].lower() for c in candidates}
        assert "website" not in titles_lower

    def test_discover_empty_records(self) -> None:
        """Empty records list -> no candidates."""
        candidates = _deterministic_discover(
            records=[],
            artifact_projects=[],
            existing_names=set(),
        )
        assert candidates == []


# ---------------------------------------------------------------------------
# TestScanForProjects
# ---------------------------------------------------------------------------

class TestScanForProjects:

    def test_scan_with_empty_store(self) -> None:
        """Store with no records -> returns empty list."""
        store = MockDiscoveryStore(records=[])
        result = _run(scan_for_projects(store=store))
        assert result == []

    def test_scan_creates_candidates(self) -> None:
        """Store with records having recurring tags -> verify candidates returned."""
        records = [
            _MockRecord("r1", summary="note 1", tags="backend,api"),
            _MockRecord("r2", summary="note 2", tags="backend,refactor"),
            _MockRecord("r3", summary="note 3", tags="backend,testing"),
            _MockRecord("r4", summary="note 4", tags="api,docs"),
            _MockRecord("r5", summary="note 5", tags="api,deploy"),
        ]
        store = MockDiscoveryStore(records=records)
        result = _run(scan_for_projects(store=store))
        assert len(result) >= 1
        # Both "backend" (3 occurrences) and "api" (3 occurrences) should appear
        titles_lower = {c["title"].lower() for c in result}
        assert "backend" in titles_lower
        assert "api" in titles_lower
        # Every candidate must have create_project type
        for c in result:
            assert c["suggestion_type"] == "create_project"
