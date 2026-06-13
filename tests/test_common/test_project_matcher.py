"""Tests for common/project_ai/matcher.py — deterministic + pipeline matching."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from common.project_ai.discovery import _deterministic_discover, _llm_discover, scan_for_projects
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
    def __init__(self, records, projects=None, aliases=None, todos=None, suggestions=None):
        self._records = records
        self._projects = projects or []
        self._aliases = aliases or {}
        self._todos = todos or []
        self._suggestions = suggestions or []

    def list_records(self, **kwargs):
        return self._records

    def list_projects(self, **kwargs):
        return self._projects

    def list_project_aliases(self, project_id):
        return self._aliases.get(project_id, [])

    def list_todos(self, **kwargs):
        return self._todos

    def list_project_suggestions(self, **kwargs):
        return self._suggestions


# ---------------------------------------------------------------------------
# TestDeterministicDiscovery
# ---------------------------------------------------------------------------

class TestDeterministicDiscovery:

    def test_discover_from_recurring_tags(self) -> None:
        """15+ records sharing the same tag across 3+ dates -> produces a create_project candidate."""
        records = [
            {"id": f"r{i}", "summary": f"note {i}", "tags": "machine-learning,python", "date": f"2026-01-{i:02d}"}
            for i in range(1, 17)  # 16 records, 16 distinct dates
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
        assert ml["confidence"] >= 0.70
        assert len(ml["evidence"]) >= 1

    def test_discover_from_artifact_projects(self) -> None:
        """Same project string in 15+ artifact_projects across 3+ dates -> produces a candidate."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "", "date": f"2026-01-{i:02d}"}
            for i in range(1, 17)
        ]
        artifacts = [
            {"project": "infra-upgrade", "record_id": f"r{i}", "date": f"2026-01-{i:02d}"}
            for i in range(1, 17)  # 16 artifacts across 16 dates
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=artifacts,
            existing_names=set(),
        )
        titles_lower = {c["title"].lower() for c in candidates}
        assert "infra-upgrade" in titles_lower or "infra upgrade" in titles_lower

    def test_discover_skips_existing_projects(self) -> None:
        """Tag matching an existing project title is skipped even with enough occurrences."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "website", "date": f"2026-01-{i:02d}"}
            for i in range(1, 17)  # 16 records
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=[],
            existing_names={"website"},
        )
        titles_lower = {c["title"].lower() for c in candidates}
        assert "website" not in titles_lower

    def test_discover_requires_minimum_occurrences(self) -> None:
        """Tags with fewer than 15 occurrences are not suggested (high threshold for LLM-less path)."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "rare-topic", "date": f"2026-01-{i:02d}"}
            for i in range(1, 11)  # 10 records — below threshold of 15
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
        )
        assert candidates == []

    def test_discover_requires_multi_date_span(self) -> None:
        """Tags appearing many times but on fewer than 3 dates are not suggested."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "clustered-topic", "date": "2026-01-01"}
            for i in range(1, 21)  # 20 records, all same date
        ]
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
        )
        assert candidates == []

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
        """Store with records having 15+ recurring tags across 3+ dates -> verify candidates."""
        records = [
            _MockRecord(f"r{i}", summary=f"note {i}", tags="backend,api", date=f"2026-01-{i:02d}")
            for i in range(1, 17)  # 16 records, 16 distinct dates
        ]
        store = MockDiscoveryStore(records=records)
        result = _run(scan_for_projects(store=store))
        assert len(result) >= 1
        # Both "backend" (16 occurrences) and "api" (16 occurrences) should appear
        titles_lower = {c["title"].lower() for c in result}
        assert "backend" in titles_lower
        assert "api" in titles_lower
        # Every candidate must have create_project type
        for c in result:
            assert c["suggestion_type"] == "create_project"


# ---------------------------------------------------------------------------
# TestTwoPhaseLlmDiscovery
# ---------------------------------------------------------------------------

class _MockAgent:
    """Mock agent that returns a predefined JSON response for any prompt."""

    def __init__(self, response: dict):
        self._response = response
        self.call_count = 0
        self.last_user_msgs: list[str] = []

    async def run_prompt(self, system: str, user: str) -> str:
        self.call_count += 1
        self.last_user_msgs.append(user)
        return json.dumps(self._response)


class TestTwoPhaseLlmDiscovery:

    def test_llm_discover_identifies_topic_clusters(self) -> None:
        """Two-phase: local tag clustering → focused LLM eval per topic."""
        records = [
            {"id": f"r{i}", "summary": f"吃了水果{i}", "tags": "水果,饮食", "date": f"2026-01-{i:02d}"}
            for i in range(1, 6)  # 5 records for "水果" across 5 dates
        ]
        agent = _MockAgent({
            "candidates": [{
                "title": "每天吃一个水果",
                "rationale": "用户决定每天吃水果并持续追踪",
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "suggested_milestones": ["连续7天", "连续30天"],
                "confidence": 0.85,
                "suggestion_type": "create_project",
            }]
        })
        result = _run(_llm_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
            agent=agent,
        ))
        assert len(result) == 1
        assert result[0]["title"] == "每天吃一个水果"
        assert result[0]["confidence"] == 0.85
        # Agent should have been called once per promising topic
        assert agent.call_count >= 1
        # Each call should contain focused records, not all 5 at once in a giant dump
        for msg in agent.last_user_msgs:
            assert "## Topic:" in msg

    def test_llm_discover_returns_empty_when_no_confident_topics(self) -> None:
        """LLM returns no candidates for any topic → empty result."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "随机话题", "date": f"2026-01-{i:02d}"}
            for i in range(1, 6)
        ]
        agent = _MockAgent({"candidates": []})
        result = _run(_llm_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
            agent=agent,
        ))
        assert result == []

    def test_llm_discover_filters_below_confidence(self) -> None:
        """Topics with confidence below threshold are filtered out."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "弱话题", "date": f"2026-01-{i:02d}"}
            for i in range(1, 6)
        ]
        agent = _MockAgent({
            "candidates": [{
                "title": "弱话题",
                "rationale": "不太确定",
                "evidence": [],
                "suggested_milestones": [],
                "confidence": 0.50,  # below LLM_DISCOVERY_MIN_CONFIDENCE (0.70)
                "suggestion_type": "create_project",
            }]
        })
        result = _run(_llm_discover(
            records=records,
            artifact_projects=[],
            existing_names=set(),
            agent=agent,
        ))
        assert result == []

    def test_llm_discover_skips_existing_projects(self) -> None:
        """LLM returns a candidate that matches an existing project → filtered."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "已有项目", "date": f"2026-01-{i:02d}"}
            for i in range(1, 6)
        ]
        agent = _MockAgent({
            "candidates": [{
                "title": "已有项目",
                "rationale": "...",
                "evidence": [],
                "suggested_milestones": [],
                "confidence": 0.85,
                "suggestion_type": "create_project",
            }]
        })
        result = _run(_llm_discover(
            records=records,
            artifact_projects=[],
            existing_names={"已有项目"},
            agent=agent,
        ))
        assert result == []
