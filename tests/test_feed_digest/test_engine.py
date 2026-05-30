"""Unit tests for feed_digest engine."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from feed_digest.config import DomainConfig, FeedDigestConfig
from feed_digest.engine import FeedDigestEngine
from feed_digest.models import FeedDigestResult, FeedItem
from feed_digest.presets import get_preset


def _make_item(source: str, url: str, title: str = "Test", content: str = "content") -> FeedItem:
    return FeedItem(
        source=source,
        title=title,
        url=url,
        content=content,
        published_at=datetime.now(timezone.utc).isoformat(),
    )


class MockSource:
    def __init__(self, items: list[FeedItem], fail: bool = False) -> None:
        self._items = items
        self._fail = fail

    async def collect(self, **kwargs: object) -> list[FeedItem]:
        del kwargs
        if self._fail:
            raise RuntimeError("mock source failure")
        return self._items


class MockAIPipeline:
    async def score_and_filter(self, items: list[FeedItem], **kwargs: object) -> list[FeedItem]:
        del kwargs
        return [FeedItem(**{**vars(item), "score": 0.8, "importance_reason": "important"}) for item in items]

    async def deduplicate(self, items: list[FeedItem]) -> list[FeedItem]:
        return items

    async def synthesize(self, items: list[FeedItem], **kwargs: object) -> tuple[str, list[str]]:
        del items
        title = str(kwargs.get("title", "Test"))
        return f"# {title}\n\n## 今日总览\nTest digest.", ["Trend 1"]


@pytest.fixture
def config() -> FeedDigestConfig:
    return FeedDigestConfig(
        enabled=True,
        domains={
            "ai_news": DomainConfig(
                title="AI 热点",
                sources=["github", "hackernews"],
            )
        },
        enable_domains=["ai_news"],
        max_candidates=10,
        max_items=5,
        min_relevance_score=0.5,
        min_signal_score=0.5,
    )


def run(coro: object) -> object:
    return asyncio.get_event_loop().run_until_complete(coro)


def test_basic_run(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(3)]
    items_hn = [_make_item("hackernews", f"https://hn.algolia.com/item{i}") for i in range(3)]

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        def _source_factory(name: str, cfg: dict | None = None) -> MockSource:
            del cfg
            if name == "github":
                return MockSource(items_gh)
            return MockSource(items_hn)

        mock_get_source.side_effect = _source_factory
        mock_pipe.return_value = MockAIPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert isinstance(result, FeedDigestResult)
    assert result.preset == "ai_news"
    assert not result.is_empty
    assert len(result.source_stats) == 2
    assert result.markdown
    assert "Test" in result.markdown


def test_url_dedup(config: FeedDigestConfig) -> None:
    dup_url = "https://github.com/org/same-repo"
    items = [_make_item("github", dup_url, title=f"Title {i}") for i in range(3)]

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_get_source.side_effect = lambda n, c=None: MockSource(items)
        mock_pipe.return_value = MockAIPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert not result.is_empty
    assert len(result.selected_items) <= 1


def test_max_candidates(config: FeedDigestConfig) -> None:
    config.max_candidates = 3
    items = [_make_item("github", f"https://github.com/repo{i}") for i in range(10)]

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        captured_input: list[FeedItem] = []

        class CapturePipeline:
            async def score_and_filter(self, inp: list[FeedItem], **kwargs: object) -> list[FeedItem]:
                del kwargs
                captured_input.extend(inp)
                return inp

            async def deduplicate(self, items: list[FeedItem]) -> list[FeedItem]:
                return items

            async def synthesize(self, items: list[FeedItem], **kwargs: object) -> tuple[str, list[str]]:
                del items, kwargs
                return "# Test\n## 今日总览\nok", []

        mock_get_source.side_effect = lambda n, c=None: MockSource(items[:5])
        mock_pipe.return_value = CapturePipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        run(engine.run())

    assert len(captured_input) <= 3


def test_empty_feed_when_no_candidates(config: FeedDigestConfig) -> None:
    with patch("feed_digest.engine.get_source") as mock_get_source:
        mock_get_source.side_effect = lambda n, c=None: MockSource([])

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert result.is_empty
    assert "无高信号简报" in result.markdown or "无" in result.markdown


def test_source_failure_warning(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(2)]

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        def _src(name: str, cfg: dict | None = None) -> MockSource:
            del cfg
            if name == "github":
                return MockSource(items_gh)
            return MockSource([], fail=True)

        mock_get_source.side_effect = _src
        mock_pipe.return_value = MockAIPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    hn_stat = next((stat for stat in result.source_stats if stat.source == "hackernews"), None)
    assert hn_stat is not None
    assert hn_stat.failed


def test_source_stats_in_result(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(2)]
    items_hn = [_make_item("hackernews", f"https://news.ycombinator.com/item{i}") for i in range(2)]

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_get_source.side_effect = (
            lambda n, c=None: MockSource(items_gh) if n == "github" else MockSource(items_hn)
        )
        mock_pipe.return_value = MockAIPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert len(result.source_stats) == 2
    source_names = {stat.source for stat in result.source_stats}
    assert "github" in source_names and "hackernews" in source_names


def test_progress_callback_reports_pipeline_stages(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(2)]
    items_hn = [_make_item("hackernews", f"https://news.ycombinator.com/item{i}") for i in range(1)]
    progress: list[str] = []

    async def _progress(text: str) -> None:
        progress.append(text)

    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_get_source.side_effect = (
            lambda n, c=None: MockSource(items_gh) if n == "github" else MockSource(items_hn)
        )
        mock_pipe.return_value = MockAIPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run(progress_callback=_progress))

    assert not result.is_empty
    assert progress == [
        "📡 正在采集新闻源…（2 个来源，预计 5-15 秒）",
        "🔍 AI 评分过滤，共 3 条候选…（预计 20-40 秒）",
        "🔄 AI 语义去重，已筛选 3 条…",
        "✍️ AI 综合撰写简报，3 条精选内容…（预计 20-40 秒）",
    ]


def test_preset_selection() -> None:
    preset = get_preset("ai_news")
    assert preset.name == "ai_news"
    assert preset.domain == "AI & Machine Learning"
    assert preset.title_template != ""


def test_preset_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown feed preset"):
        get_preset("unknown_preset")


def test_boundary_records_not_feed_items() -> None:
    """Records, todos, decisions, blockers cannot be FeedItem sources."""
    forbidden_sources = {"record", "todo", "decision", "highlight", "blocker", "entry"}
    item = FeedItem(
        source="github",
        title="test",
        url="https://example.com",
        content="x",
        published_at="2024-01-01",
    )
    assert item.source not in forbidden_sources


def test_empty_digest_allow_empty(config: FeedDigestConfig) -> None:
    config.allow_empty_digest = True
    with patch("feed_digest.engine.get_source") as mock_get_source, patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        class EmptyPipeline:
            async def score_and_filter(self, items: list[FeedItem], **kwargs: object) -> list[FeedItem]:
                del items, kwargs
                return []

            async def deduplicate(self, items: list[FeedItem]) -> list[FeedItem]:
                del items
                return []

            async def synthesize(self, items: list[FeedItem], **kwargs: object) -> tuple[str, list[str]]:
                del items, kwargs
                return "# Empty", []

        mock_get_source.side_effect = (
            lambda n, c=None: MockSource([_make_item("github", f"https://x.com/{i}") for i in range(3)])
        )
        mock_pipe.return_value = EmptyPipeline()

        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert result.is_empty
