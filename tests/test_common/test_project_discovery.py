"""Tests for common/project_ai/discovery.py — project discovery helpers."""
from __future__ import annotations

import asyncio
import json

from common.project_ai.discovery import (
    _build_existing_projects_text,
    _existing_projects_context,
    _llm_discover,
    scan_for_projects,
)


def _run(coro):
    """Run an async coroutine from sync test code."""
    return asyncio.get_event_loop().run_until_complete(coro)


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
