"""Tests for openharness.skills.search (BM25 + heuristic hybrid ranking)."""

from __future__ import annotations

import pytest

from openharness.skills.registry import SkillRegistry
from openharness.skills.search import SkillSearchResult, find_relevant_skills
from openharness.skills.types import SkillDefinition


def _skill(
    name: str,
    description: str,
    *,
    tags: tuple[str, ...] = (),
    body: str = "",
    source: str = "bundled",
) -> SkillDefinition:
    frontmatter = (
        f"---\nname: {name}\ndescription: {description}\n"
        + (f"tags: [{', '.join(tags)}]\n" if tags else "")
        + "---\n"
    )
    content = frontmatter + body
    return SkillDefinition(
        name=name,
        description=description,
        content=content,
        source=source,
        tags=tags,
    )


def _registry(*skills: SkillDefinition) -> SkillRegistry:
    reg = SkillRegistry()
    for skill in skills:
        reg.register(skill)
    return reg


@pytest.fixture
def sample_registry() -> SkillRegistry:
    return _registry(
        _skill(
            "weekly-report",
            "工程周报：基于 git 历史分析提交模式与工作热点",
            tags=("weekly", "git"),
            body="生成工程周报，统计提交、热点文件、连续交付天数。",
        ),
        _skill(
            "lark-im",
            "飞书即时通讯：收发消息和管理群聊",
            tags=("lark", "im"),
            body="发送和回复消息、搜索聊天记录、管理群聊成员。",
        ),
        _skill(
            "code-review",
            "Review code changes for bugs, security issues and quality",
            tags=("review", "code"),
            body="Structured code review workflow with checklists.",
        ),
        _skill(
            "debug",
            "Diagnose and fix bugs systematically",
            tags=("debug",),
            body="Root-cause analysis across logs, traces and tests.",
        ),
        _skill(
            "simplify",
            "Refactor code to be simpler and more maintainable",
            tags=("refactor",),
            body="Identify duplication, extract helpers, prune dead code.",
        ),
    )


def test_basic_keyword_match(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills("code review", sample_registry, max_results=3)
    names = [r.skill.name for r in results]
    assert "code-review" in names
    assert "review" in [t for r in results for t in r.skill.tags] or "code-review" == names[0]


def test_chinese_query(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills("帮我写周报", sample_registry, max_results=3)
    assert results, "expected at least one match for Chinese query"
    assert results[0].skill.name == "weekly-report"


def test_tag_filter(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills(
        "发消息",
        sample_registry,
        max_results=5,
        tag_filter="lark",
    )
    assert results, "expected at least one lark-tagged skill"
    for r in results:
        assert any(t.lower() == "lark" for t in r.skill.tags)


def test_tag_filter_no_match_returns_empty(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills(
        "anything",
        sample_registry,
        tag_filter="nonexistent-tag",
    )
    assert results == []


def test_no_match_returns_empty(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills(
        "xyzzyplugh completely unrelated gibberish",
        sample_registry,
    )
    assert results == []


def test_empty_query_returns_empty(sample_registry: SkillRegistry) -> None:
    assert find_relevant_skills("", sample_registry) == []
    assert find_relevant_skills("   ", sample_registry) == []


def test_max_results_truncation(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills("代码", sample_registry, max_results=2)
    assert len(results) <= 2


def test_scores_descending(sample_registry: SkillRegistry) -> None:
    results = find_relevant_skills("debug bug fix", sample_registry, max_results=5)
    scores = [r.score for r in results]
    assert scores == sorted(scores, reverse=True)


def test_name_exact_match_ranks_higher() -> None:
    # Both skills have identical metadata length and neither body mentions
    # the query token, so the name-equals-query bonus is the only
    # differentiating signal.
    reg = _registry(
        _skill("alpha", "First placeholder description here", tags=("misc",)),
        _skill("zeta", "Second placeholder description here", tags=("misc",)),
    )
    results = find_relevant_skills("alpha", reg, max_results=2)
    assert results, "expected matches"
    assert results[0].skill.name == "alpha"


def test_tag_match_boosts_rank() -> None:
    reg = _registry(
        _skill("foo-a", "A generic helper about messaging", tags=("misc",)),
        _skill("foo-b", "A generic helper", tags=("lark", "im")),
    )
    results = find_relevant_skills("lark 消息", reg, max_results=2)
    assert results[0].skill.name == "foo-b", "tag overlap should lift foo-b above foo-a"


def test_result_is_frozen_dataclass() -> None:
    skill = _skill("x", "y")
    result = SkillSearchResult(skill=skill, score=1.0)
    with pytest.raises(AttributeError):
        result.score = 0.5  # type: ignore[misc]
