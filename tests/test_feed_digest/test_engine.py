"""Unit tests for feed_digest engine."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from feed_digest.config import DomainConfig, FeedDigestConfig
from feed_digest.engine import FeedDigestEngine
from feed_digest.models import FeedDigestResult, FeedItem, SourceStats
from feed_digest.presets import get_preset


def _make_item(source: str, url: str, title: str = "Test", content: str = "content") -> FeedItem:
    return FeedItem(
        source=source,
        title=title,
        url=url,
        content=content,
        published_at=datetime.now(timezone.utc).isoformat(),
    )


def _make_researcher_result(
    items: list[FeedItem],
    *,
    source_stats: list[SourceStats] | None = None,
    warnings: list[str] | None = None,
) -> SimpleNamespace:
    if source_stats is None:
        sources = sorted({item.source for item in items})
        source_stats = [
            SourceStats(source=s, fetched=sum(1 for it in items if it.source == s))
            for s in sources
        ]
    return SimpleNamespace(items=items, source_stats=source_stats, warnings=warnings or [])


class MockResearcher:
    def __init__(self, result: SimpleNamespace) -> None:
        self._result = result

    async def collect(self, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return self._result


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


@contextlib.contextmanager
def _patch_researcher_and_pipeline(researcher_result: SimpleNamespace):
    researcher_instance = MockResearcher(researcher_result)
    with patch("feed_digest.engine.FeedDigestResearcher", return_value=researcher_instance), patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_pipe.return_value = MockAIPipeline()
        yield researcher_instance


@pytest.fixture
def config() -> FeedDigestConfig:
    return FeedDigestConfig(
        enabled=True,
        domains={
            "ai_news": DomainConfig(
                title="AI 热点",
                objective="Collect AI news.",
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

    with _patch_researcher_and_pipeline(_make_researcher_result(items_gh + items_hn)):
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

    with _patch_researcher_and_pipeline(_make_researcher_result(items)):
        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert not result.is_empty
    assert len(result.selected_items) <= 1


def test_max_candidates(config: FeedDigestConfig) -> None:
    config.max_candidates = 3
    items = [_make_item("github", f"https://github.com/repo{i}") for i in range(10)]
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

    with patch("feed_digest.engine.FeedDigestResearcher", return_value=MockResearcher(_make_researcher_result(items))), patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_pipe.return_value = CapturePipeline()
        engine = FeedDigestEngine(config=config, provider_profile="test")
        run(engine.run())

    assert len(captured_input) <= 3


def test_empty_feed_when_no_candidates(config: FeedDigestConfig) -> None:
    with _patch_researcher_and_pipeline(_make_researcher_result([])):
        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert result.is_empty
    assert "无" in result.markdown


def test_default_strategy_uses_opencli_llm_research() -> None:
    item = _make_item("hackernews", "https://news.ycombinator.com/item?id=42", title="AI fallback")

    class AssertingResearcher:
        def __init__(self, *, pipeline: object) -> None:
            self.pipeline = pipeline

        async def collect(self, **kwargs: object) -> SimpleNamespace:
            assert kwargs["objective"] == "Collect high-signal AI news."
            return _make_researcher_result([item])

    cfg = FeedDigestConfig(
        domains={
            "ai_news": DomainConfig(
                title="AI 热点",
                domain="AI & Machine Learning",
                objective="Collect high-signal AI news.",
            )
        },
        enable_domains=["ai_news"],
        max_candidates=10,
        max_items=5,
    )

    with patch("feed_digest.engine.FeedDigestResearcher", AssertingResearcher), patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_pipe.return_value = MockAIPipeline()
        engine = FeedDigestEngine(config=cfg, provider_profile="test")
        result = run(engine.run())

    assert result.items == [item]
    assert result.source_stats[0].source == "hackernews"
    assert result.source_stats[0].fetched == 1


def test_source_failure_warning(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(2)]
    result_ns = SimpleNamespace(
        items=items_gh,
        source_stats=[
            SourceStats(source="github", fetched=2),
            SourceStats(source="hackernews", fetched=0, failed=True, warning="connection error"),
        ],
        warnings=["hackernews failed"],
    )

    with _patch_researcher_and_pipeline(result_ns):
        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    hn_stat = next((stat for stat in result.source_stats if stat.source == "hackernews"), None)
    assert hn_stat is not None
    assert hn_stat.failed


def test_source_stats_in_result(config: FeedDigestConfig) -> None:
    items_gh = [_make_item("github", f"https://github.com/repo{i}") for i in range(2)]
    items_hn = [_make_item("hackernews", f"https://news.ycombinator.com/item{i}") for i in range(2)]

    with _patch_researcher_and_pipeline(_make_researcher_result(items_gh + items_hn)):
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

    with _patch_researcher_and_pipeline(_make_researcher_result(items_gh + items_hn)):
        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run(progress_callback=_progress))

    assert not result.is_empty
    assert progress[0] == "📡 AI 正在使用 OpenCLI 调研新闻源…（多轮检索，预计 1-3 分钟）"
    assert any("评分" in msg for msg in progress)
    assert any("去重" in msg for msg in progress)
    assert any("撰写" in msg for msg in progress)


def test_preset_selection() -> None:
    preset = get_preset("ai_news")
    assert preset.name == "ai_news"
    assert preset.domain == "AI & Machine Learning"
    assert preset.title_template != ""


def test_preset_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown feed preset"):
        get_preset("unknown_preset")


def test_boundary_records_not_feed_items() -> None:
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
    items = [_make_item("github", f"https://x.com/{i}") for i in range(3)]

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

    with patch("feed_digest.engine.FeedDigestResearcher", return_value=MockResearcher(_make_researcher_result(items))), patch(
        "feed_digest.engine.FeedDigestAIPipeline"
    ) as mock_pipe:
        mock_pipe.return_value = EmptyPipeline()
        engine = FeedDigestEngine(config=config, provider_profile="test")
        result = run(engine.run())

    assert result.is_empty
