"""Tests for skill_search."""

from __future__ import annotations

from pathlib import Path

import pytest

from openharness.skills.registry import SkillRegistry
from openharness.skills.types import SkillDefinition
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_search_tool import SkillSearchInput, SkillSearchTool


def _skill(name: str, description: str, *, tags: tuple[str, ...] = ()) -> SkillDefinition:
    frontmatter = f"---\nname: {name}\ndescription: {description}\n"
    if tags:
        frontmatter += f"tags: [{', '.join(tags)}]\n"
    frontmatter += "---\n"
    return SkillDefinition(
        name=name,
        description=description,
        content=frontmatter + f"Body for {name}.",
        source="bundled",
        tags=tags,
    )


@pytest.fixture
def fake_registry(monkeypatch: pytest.MonkeyPatch) -> SkillRegistry:
    registry = SkillRegistry()
    registry.register(_skill("weekly-report", "工程周报：git 历史分析", tags=("weekly",)))
    registry.register(_skill("lark-im", "飞书即时通讯：收发消息", tags=("lark", "im")))
    registry.register(_skill("code-review", "Review code changes", tags=("review",)))

    def _fake_load_skill_registry(cwd, **kwargs):
        return registry

    import openharness.tools.skill_search_tool as mod

    monkeypatch.setattr(mod, "load_skill_registry", _fake_load_skill_registry)
    monkeypatch.setitem(
        SkillSearchTool.execute.__globals__,
        "load_skill_registry",
        _fake_load_skill_registry,
    )
    return registry


@pytest.mark.asyncio
async def test_search_action_returns_results(
    tmp_path: Path, fake_registry: SkillRegistry
) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query="code review"),
        context,
    )
    assert result.is_error is False
    assert "code-review" in result.output
    assert "Found" in result.output


@pytest.mark.asyncio
async def test_search_action_chinese_query(
    tmp_path: Path, fake_registry: SkillRegistry
) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query="帮我写周报"),
        context,
    )
    assert result.is_error is False
    assert "weekly-report" in result.output


@pytest.mark.asyncio
async def test_search_action_requires_query(tmp_path: Path) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query=""),
        context,
    )
    assert result.is_error is True
    assert "query must be a non-empty" in result.output


@pytest.mark.asyncio
async def test_search_action_no_match_message(
    tmp_path: Path, fake_registry: SkillRegistry
) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query="xyzzyplugh gibberish nonsense"),
        context,
    )
    assert result.is_error is False
    assert "No skills matched" in result.output


@pytest.mark.asyncio
async def test_search_action_tag_filter(
    tmp_path: Path, fake_registry: SkillRegistry
) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query="消息", tag="lark"),
        context,
    )
    assert result.is_error is False
    assert "lark-im" in result.output
    assert "weekly-report" not in result.output


@pytest.mark.asyncio
async def test_search_action_respects_limit(
    tmp_path: Path, fake_registry: SkillRegistry
) -> None:
    context = ToolExecutionContext(cwd=tmp_path)
    result = await SkillSearchTool().execute(
        SkillSearchInput(query="code", limit=1),
        context,
    )
    assert result.is_error is False
    assert "Found 1 skill" in result.output


def test_search_action_is_read_only() -> None:
    tool = SkillSearchTool()
    assert tool.is_read_only(SkillSearchInput(query="x")) is True


def test_search_in_api_schema() -> None:
    schema = SkillSearchTool().to_api_schema()
    assert schema["name"] == "skill_search"
    assert "query" in schema["parameters"]["properties"]
    assert "limit" in schema["parameters"]["properties"]
    assert "tag" in schema["parameters"]["properties"]
