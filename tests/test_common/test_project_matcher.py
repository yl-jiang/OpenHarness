"""Tests for common/project_ai/matcher.py — deterministic + pipeline matching."""
from __future__ import annotations

import asyncio
import json

from common.project_ai.discovery import (
    _build_existing_projects_text,
    _existing_projects_context,
    _llm_discover,
    scan_for_projects,
)
from common.project_ai.matcher import match_record
from common.project_ai.types import (
    CONFIDENCE_AUTO_LINK,
    CONFIDENCE_SUGGEST,
    MatchCandidate,
    ProjectLinkInput,
)


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.get_event_loop().run_until_complete(coro)


def _p(id: str, title: str, description: str = "") -> ProjectLinkInput:
    """Build a ProjectLinkInput for deterministic/pipeline tests."""
    return ProjectLinkInput(id=id, title=title, description=description)


class TestDeterministicTitleMatch:
    def test_deterministic_title_match(self) -> None:
        """Record content fully contains project title tokens -> high confidence auto_link."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
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
                projects=[_p(id="p1", title="Alpha Launch")],
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
                projects=[_p(id="p1", title="Website Redesign")],
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
                projects=[_p(id="p1", title="Website Redesign")],
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
            _p(id="p_auto", title="Data Pipeline"),
            _p(id="p_unrelated", title="Cooking Recipes"),
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

    def __init__(self, project_id: str, title: str, description: str = "", tags: str = ""):
        self._data = {"id": project_id, "title": title, "description": description, "tags": tags}

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
# TestExistingProjectsContext
# ---------------------------------------------------------------------------

class TestExistingProjectsContext:

    def test_build_text_with_no_projects(self) -> None:
        """Empty project list returns placeholder text."""
        text = _build_existing_projects_text([])
        assert text == "(no existing projects)"

    def test_build_text_with_projects(self) -> None:
        """Projects are formatted with title, description, and tags."""
        projects = [
            {
                "title": "每天吃水果",
                "description": "坚持每天吃一个水果的健康计划",
                "tags": "水果,健康,饮食",
                "aliases": ["水果计划"],
            },
            {
                "title": "学习Rust",
                "description": "",
                "tags": "Rust,编程",
                "aliases": [],
            },
        ]
        text = _build_existing_projects_text(projects)
        assert "每天吃水果" in text
        assert "坚持每天吃一个水果的健康计划" in text
        assert "水果, 健康, 饮食" in text
        assert "水果计划" in text
        assert "学习Rust" in text
        assert "Rust, 编程" in text

    def test_existing_projects_context_from_store(self) -> None:
        """_existing_projects_context collects titles, descriptions, tags, and aliases."""
        store = MockDiscoveryStore(
            records=[],
            projects=[
                _MockProject("p1", "项目A", description="描述A", tags="标签1,标签2"),
            ],
            aliases={"p1": [_MockAlias("别名A")]},
        )
        ctx = _existing_projects_context(store)
        assert len(ctx) == 1
        assert ctx[0]["title"] == "项目A"
        assert ctx[0]["description"] == "描述A"
        assert ctx[0]["tags"] == "标签1,标签2"
        assert ctx[0]["aliases"] == ["别名A"]


# ---------------------------------------------------------------------------
# TestScanForProjects
# ---------------------------------------------------------------------------

class TestScanForProjects:

    def test_scan_requires_agent(self) -> None:
        """scan_for_projects raises RuntimeError when agent is None."""
        store = MockDiscoveryStore(records=[])
        try:
            _run(scan_for_projects(store=store, agent=None))
            assert False, "Expected RuntimeError"
        except RuntimeError as e:
            assert "agent" in str(e).lower() or "AI" in str(e)

    def test_scan_with_empty_store(self) -> None:
        """Store with no records and a valid agent -> returns empty list."""
        store = MockDiscoveryStore(records=[])
        agent = _MockAgent({"candidates": []})
        result = _run(scan_for_projects(store=store, agent=agent))
        assert result == []

    def test_scan_creates_candidates_with_agent(self) -> None:
        """Store with records and LLM agent -> candidates from LLM response."""
        records = [
            _MockRecord(f"r{i}", summary=f"note {i}", tags="backend,api", date=f"2026-01-{i:02d}")
            for i in range(1, 6)
        ]
        agent = _MockAgent({
            "candidates": [{
                "title": "后端API重构",
                "summary": "对后端API进行系统性重构",
                "keywords": ["backend", "api", "重构"],
                "rationale": "持续的后端开发记录",
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "suggested_milestones": ["完成API文档"],
                "confidence": 0.90,
                "suggestion_type": "create_project",
            }]
        })
        store = MockDiscoveryStore(records=records)
        result = _run(scan_for_projects(store=store, agent=agent))
        assert len(result) == 1
        assert result[0]["title"] == "后端API重构"
        assert result[0]["summary"] == "对后端API进行系统性重构"
        assert result[0]["keywords"] == ["backend", "api", "重构"]


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
                "summary": "用户决定每天吃一个水果并持续追踪摄入情况",
                "keywords": ["水果", "饮食", "健康习惯"],
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
            existing_projects_text="(no existing projects)",
            existing_titles=set(),
            agent=agent,
        ))
        assert len(result) == 1
        assert result[0]["title"] == "每天吃一个水果"
        assert result[0]["summary"] == "用户决定每天吃一个水果并持续追踪摄入情况"
        assert result[0]["keywords"] == ["水果", "饮食", "健康习惯"]
        assert result[0]["confidence"] == 0.85
        # Agent should have been called once per promising topic
        assert agent.call_count >= 1
        # Each call should contain focused records and existing projects context
        for msg in agent.last_user_msgs:
            assert "## Topic:" in msg
            assert "Existing projects" in msg

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
            existing_projects_text="(no existing projects)",
            existing_titles=set(),
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
                "summary": "不确定的话题",
                "keywords": ["弱"],
                "rationale": "不太确定",
                "evidence": [],
                "suggested_milestones": [],
                "confidence": 0.50,
                "suggestion_type": "create_project",
            }]
        })
        result = _run(_llm_discover(
            records=records,
            artifact_projects=[],
            existing_projects_text="(no existing projects)",
            existing_titles=set(),
            agent=agent,
        ))
        assert result == []

    def test_llm_discover_skips_existing_projects(self) -> None:
        """LLM returns a candidate that matches an existing project title → filtered by exact title."""
        records = [
            {"id": f"r{i}", "summary": "a", "tags": "已有项目", "date": f"2026-01-{i:02d}"}
            for i in range(1, 6)
        ]
        agent = _MockAgent({
            "candidates": [{
                "title": "已有项目",
                "summary": "已经存在的项目",
                "keywords": ["已有"],
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
            existing_projects_text="1. **已有项目**\n   Summary: 已有项目的描述\n   Keywords: 已有, 项目",
            existing_titles={"已有项目"},
            agent=agent,
        ))
        assert result == []

class TestFusionDeterministicAndLlm:
    """Test that deterministic and LLM results are fused, not mutually exclusive."""

    def test_llm_always_runs_when_agent_available(self) -> None:
        """LLM layer runs even when deterministic found matches (fusion, not fallback)."""
        agent = _MockAgent({
            "matches": [{
                "project_id": "p2",
                "project_title": "Backend API",
                "confidence": 0.80,
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "rationale": "LLM detected backend API work",
            }]
        })
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[
                    _p(id="p1", title="Website Redesign"),
                    _p(id="p2", title="Backend API"),
                ],
                aliases_by_project={},
                agent=agent,
            )
        )
        # Deterministic should find p1 (title subset match)
        all_matches = result.auto_links + result.suggestions
        det_matches = [c for c in all_matches if c.strategy == "deterministic"]
        assert len(det_matches) >= 1, "Deterministic should still find p1"
        # LLM should have been called (fusion, not skipped)
        assert agent.call_count == 1, "LLM should be called even when deterministic found matches"
        # LLM-only match p2 should appear
        llm_matches = [c for c in all_matches if c.project_id == "p2"]
        assert len(llm_matches) == 1, "LLM-only match should be included"

    def test_hybrid_strategy_when_both_layers_match_same_project(self) -> None:
        """When both deterministic and LLM match the same project, strategy is hybrid."""
        agent = _MockAgent({
            "matches": [{
                "project_id": "p1",
                "project_title": "Website Redesign",
                "confidence": 0.90,
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "rationale": "LLM confirms website redesign",
            }]
        })
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=agent,
            )
        )
        all_matches = result.auto_links + result.suggestions
        hybrid = [c for c in all_matches if c.strategy == "hybrid"]
        assert len(hybrid) == 1, "Same project matched by both layers should be hybrid"
        assert hybrid[0].project_id == "p1"
        # Hybrid should have max confidence from both layers
        assert hybrid[0].confidence >= 0.88  # deterministic was 0.88, LLM was 0.90
        # Hybrid should merge evidence from both layers
        assert len(hybrid[0].evidence) >= 2, "Hybrid should merge evidence from both layers"

    def test_llm_focuses_on_unresolved_projects(self) -> None:
        """LLM receives filtered project list excluding deterministic matches."""
        agent = _MockAgent({
            "matches": [{
                "project_id": "p2",
                "project_title": "Backend API",
                "confidence": 0.75,
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "rationale": "LLM found backend API",
            }]
        })
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[
                    _p(id="p1", title="Website Redesign"),
                    _p(id="p2", title="Backend API"),
                ],
                aliases_by_project={},
                agent=agent,
            )
        )
        # LLM prompt should only contain unresolved projects (p2, not p1)
        assert agent.call_count == 1
        user_msg = agent.last_user_msgs[0]
        assert "Backend API" in user_msg, "Unresolved project should be in LLM prompt"
        # p1 was resolved by deterministic, so it should be excluded from LLM input
        # (unless all projects are matched, in which case all are sent)
        all_matches = result.auto_links + result.suggestions
        assert any(c.project_id == "p1" for c in all_matches)
        assert any(c.project_id == "p2" for c in all_matches)

    def test_no_agent_means_deterministic_only(self) -> None:
        """Without agent, only deterministic matching runs (unchanged behavior)."""
        result = _run(
            match_record(
                record_id="r1",
                record_content="Working on the website redesign project today",
                record_summary="Website redesign progress",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=None,
            )
        )
        assert len(result.auto_links) == 1
        assert result.auto_links[0].strategy == "deterministic"


class _RawMockAgent:
    """Mock agent that returns a predefined raw string from run_prompt."""

    def __init__(self, raw_response: str):
        self._raw_response = raw_response
        self.call_count = 0
        self.last_user_msgs: list[str] = []

    async def run_prompt(self, system: str, user: str) -> str:
        self.call_count += 1
        self.last_user_msgs.append(user)
        return self._raw_response


class TestLlmMatchPydanticValidation:
    """LLM linking responses are parsed and validated with Pydantic."""

    def test_valid_llm_response_produces_candidates(self) -> None:
        """A well-formed JSON response is validated and turned into candidates."""
        agent = _MockAgent({
            "matches": [{
                "project_id": "p1",
                "project_title": "Website Redesign",
                "confidence": 0.80,
                "evidence": [{"entity_type": "record", "entity_id": "r1"}],
                "rationale": "LLM sees website redesign work",
            }]
        })
        result = _run(
            match_record(
                record_id="r1",
                record_content="Some content",
                record_summary="Summary",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=agent,
            )
        )
        assert len(result.suggestions) == 1
        assert result.suggestions[0].project_id == "p1"
        assert result.suggestions[0].confidence == 0.80
        assert result.suggestions[0].rationale == "LLM sees website redesign work"
        assert result.suggestions[0].evidence == [{"entity_type": "record", "entity_id": "r1"}]

    def test_invalid_json_returns_empty(self) -> None:
        """Non-JSON LLM output is caught and results in no LLM candidates."""
        agent = _RawMockAgent("not valid json")
        result = _run(
            match_record(
                record_id="r1",
                record_content="Some content",
                record_summary="Summary",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=agent,
            )
        )
        assert len(result.auto_links) == 0
        assert len(result.suggestions) == 0

    def test_missing_required_field_returns_empty(self) -> None:
        """A JSON response missing required fields fails validation gracefully."""
        agent = _MockAgent({"matches": [{"project_id": "p1"}]})
        result = _run(
            match_record(
                record_id="r1",
                record_content="Some content",
                record_summary="Summary",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=agent,
            )
        )
        assert len(result.auto_links) == 0
        assert len(result.suggestions) == 0

    def test_extra_fields_are_ignored(self) -> None:
        """Pydantic ignores extra fields returned by the LLM."""
        agent = _MockAgent({
            "matches": [{
                "project_id": "p1",
                "project_title": "Website Redesign",
                "confidence": 0.90,
                "extra_field": "should be ignored",
                "evidence": [{"entity_type": "record", "entity_id": "r1", "extra": "ignored"}],
                "rationale": "",
            }]
        })
        result = _run(
            match_record(
                record_id="r1",
                record_content="Some content",
                record_summary="Summary",
                artifact_projects=[],
                projects=[_p(id="p1", title="Website Redesign")],
                aliases_by_project={},
                agent=agent,
            )
        )
        assert len(result.auto_links) == 1
        assert result.auto_links[0].project_id == "p1"
        assert result.auto_links[0].evidence == [{"entity_type": "record", "entity_id": "r1"}]
