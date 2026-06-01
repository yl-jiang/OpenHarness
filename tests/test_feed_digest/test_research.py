"""Tests for the agentic OpenCLI research layer used by feed_digest."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from feed_digest.config import ResearchBudget, ResearchConfig
from feed_digest.models import FeedItem
from feed_digest.research import (
    FeedDigestResearcher,
    OpenCliCommand,
    OpenCliRegistry,
    OpenCliRunner,
    RawEvidence,
    ResearchAction,
    ResearchDecision,
)


def test_opencli_registry_loads_machine_readable_catalog(monkeypatch) -> None:
    def fake_run(argv, capture_output, text, timeout, check, env):  # noqa: ANN001
        assert argv == ["opencli", "list", "-f", "json"]
        assert capture_output is True
        assert text is True
        assert timeout == 8
        assert check is False
        assert env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] == "8"
        return SimpleNamespace(
            returncode=0,
            stdout='[{"site":"hackernews","name":"search","strategy":"public","browser":false,"description":"Search HN"}]',
            stderr="",
        )

    monkeypatch.setattr("feed_digest.research.subprocess.run", fake_run)

    catalog = OpenCliRegistry(timeout_seconds=8).load()

    assert catalog == [
        OpenCliCommand(
            site="hackernews",
            name="search",
            strategy="public",
            browser=False,
            description="Search HN",
        )
    ]


@pytest.mark.asyncio
async def test_opencli_runner_rejects_actions_outside_catalog() -> None:
    runner = OpenCliRunner()
    catalog = [OpenCliCommand(site="hackernews", name="search", strategy="public", browser=False)]

    with pytest.raises(ValueError, match="not present in OpenCLI catalog"):
        await runner.run(
            ResearchAction(source="bad", site="github", command="search", args=["AI"]),
            catalog=catalog,
            timeout_seconds=10,
            max_output_chars=1000,
        )


@pytest.mark.asyncio
async def test_opencli_runner_returns_failed_evidence_instead_of_raising(monkeypatch) -> None:
    def fake_run(argv, capture_output, text, timeout, check, env):  # noqa: ANN001
        assert argv == ["opencli", "hackernews", "search", "AI", "-f", "json"]
        return SimpleNamespace(returncode=1, stdout="", stderr="TLS failed")

    monkeypatch.setattr("feed_digest.research.subprocess.run", fake_run)
    runner = OpenCliRunner()

    evidence = await runner.run(
        ResearchAction(source="hn", site="hackernews", command="search", args=["AI", "-f", "json"]),
        catalog=[OpenCliCommand(site="hackernews", name="search", strategy="public", browser=False)],
        timeout_seconds=10,
        max_output_chars=1000,
    )

    assert evidence.source == "hn"
    assert evidence.failed
    assert evidence.error == "TLS failed"


@pytest.mark.asyncio
async def test_researcher_lets_pipeline_plan_opencli_iterations_and_extract_items() -> None:
    item = FeedItem(
        source="hackernews",
        title="AI agent launch",
        url="https://news.ycombinator.com/item?id=1",
        content="High-signal agent release.",
        published_at="2026-05-31T00:00:00Z",
    )

    class FakeRegistry:
        def load(self) -> list[OpenCliCommand]:
            return [OpenCliCommand(site="hackernews", name="search", strategy="public", browser=False)]

    class FakeRunner:
        async def run(self, action: ResearchAction, **kwargs: object) -> RawEvidence:
            del kwargs
            return RawEvidence(
                source=action.source,
                command="opencli hackernews search AI -f json",
                content='[{"title":"AI agent launch","url":"https://news.ycombinator.com/item?id=1"}]',
            )

    class FakePipeline:
        calls = 0

        async def plan_research_actions(self, **kwargs: object) -> ResearchDecision:
            self.calls += 1
            if self.calls > 1:
                return ResearchDecision(done=True, rationale="enough")
            assert kwargs["objective"] == "Find important AI news"
            return ResearchDecision(
                actions=[
                    ResearchAction(
                        source="hackernews",
                        site="hackernews",
                        command="search",
                        args=["AI", "-f", "json"],
                    )
                ],
                rationale="HN is relevant",
            )

        async def extract_items_from_evidence(self, evidence: list[RawEvidence], **kwargs: object) -> list[FeedItem]:
            assert len(evidence) == 1
            assert kwargs["domain"] == "AI & Machine Learning"
            return [item]

    researcher = FeedDigestResearcher(
        pipeline=FakePipeline(),
        registry=FakeRegistry(),
        runner=FakeRunner(),
    )

    result = await researcher.collect(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        config=ResearchConfig(budget=ResearchBudget(max_rounds=2, max_actions=4)),
    )

    assert result.items == [item]
    assert result.source_stats[0].source == "hackernews"
    assert result.source_stats[0].fetched == 1
    assert not result.source_stats[0].failed


@pytest.mark.asyncio
async def test_researcher_lets_ai_autonomously_select_sources() -> None:
    """AI should freely pick any catalog source; no allowlist/required-sites enforced."""
    item = FeedItem(
        source="hackernews",
        title="AI agent launch",
        url="https://news.ycombinator.com/item?id=1",
        content="High-signal agent release.",
        published_at="2026-05-31T00:00:00Z",
    )

    class FakeRegistry:
        def load(self) -> list[OpenCliCommand]:
            return [
                OpenCliCommand(site="hackernews", name="top", strategy="public", browser=False),
                OpenCliCommand(site="aibase", name="news", strategy="public", browser=False),
                OpenCliCommand(site="producthunt", name="today", strategy="public", browser=False),
            ]

    class FakeRunner:
        async def run(self, action: ResearchAction, **kwargs: object) -> RawEvidence:
            del kwargs
            return RawEvidence(
                source=action.source,
                command=f"opencli {action.site} {action.command}",
                content=f"title: item from {action.source}",
            )

    class FakePipeline:
        received_catalog_sites: list[str] = []
        calls = 0

        async def plan_research_actions(self, **kwargs: object) -> ResearchDecision:
            self.calls += 1
            catalog = kwargs["catalog"]
            self.received_catalog_sites = [cmd.site for cmd in catalog]
            # Verify required_sites is NOT passed anymore
            assert "required_sites" not in kwargs
            if self.calls > 1:
                return ResearchDecision(done=True, rationale="enough")
            return ResearchDecision(
                actions=[ResearchAction(source="hackernews", site="hackernews", command="top")],
                rationale="HN is a good source for AI news",
            )

        async def extract_items_from_evidence(self, evidence: list[RawEvidence], **kwargs: object) -> list[FeedItem]:
            del kwargs
            return [item]

    pipeline = FakePipeline()
    researcher = FeedDigestResearcher(pipeline=pipeline, registry=FakeRegistry(), runner=FakeRunner())

    result = await researcher.collect(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        config=ResearchConfig(budget=ResearchBudget(max_rounds=2, max_actions=4)),
    )

    assert result.items == [item]
    # All catalog sites are available to the AI, no filtering by allowlist
    assert set(pipeline.received_catalog_sites) == {"hackernews", "aibase", "producthunt"}
    assert result.source_stats[0].source == "hackernews"
    assert not result.source_stats[0].failed


@pytest.mark.asyncio
async def test_researcher_stops_gracefully_when_ai_returns_no_actions() -> None:
    """If AI returns empty actions, researcher stops immediately (no fallback)."""

    class FakeRegistry:
        def load(self) -> list[OpenCliCommand]:
            return [OpenCliCommand(site="hackernews", name="top", strategy="public", browser=False)]

    class FakePipeline:
        async def plan_research_actions(self, **kwargs: object) -> ResearchDecision:
            del kwargs
            return ResearchDecision(actions=[], rationale="nothing useful")

        async def extract_items_from_evidence(self, evidence: list[RawEvidence], **kwargs: object) -> list[FeedItem]:
            del kwargs
            assert evidence == [], "no evidence should be collected"
            return []

    researcher = FeedDigestResearcher(
        pipeline=FakePipeline(),
        registry=FakeRegistry(),
        runner=OpenCliRunner(),
    )

    result = await researcher.collect(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        config=ResearchConfig(budget=ResearchBudget(max_rounds=2, max_actions=4)),
    )

    assert result.items == []
    assert result.source_stats == []


@pytest.mark.asyncio
async def test_collect_emits_per_source_progress() -> None:
    """progress_callback should receive friendly per-source research updates."""
    item = FeedItem(
        source="hackernews",
        title="An item",
        url="https://example.com/a",
        content="High-signal item.",
        published_at="2024-01-01T00:00:00+00:00",
    )

    class FakeRegistry:
        def load(self) -> list[OpenCliCommand]:
            return [
                OpenCliCommand(site="github", name="trending", strategy="public", browser=False),
                OpenCliCommand(site="hackernews", name="top", strategy="public", browser=False),
            ]

    class FakeRunner:
        async def run(self, action: ResearchAction, **kwargs: object) -> RawEvidence:
            del kwargs
            return RawEvidence(
                source=action.source or action.site,
                command=f"opencli {action.site} {action.command}",
                content=f"title: item from {action.source or action.site}",
            )

    class FakePipeline:
        calls = 0

        async def plan_research_actions(self, **kwargs: object) -> ResearchDecision:
            del kwargs
            self.calls += 1
            if self.calls > 1:
                return ResearchDecision(done=True, rationale="enough")
            return ResearchDecision(
                actions=[ResearchAction(source="hackernews", site="hackernews", command="top")],
            )

        async def extract_items_from_evidence(self, evidence: list[RawEvidence], **kwargs: object) -> list[FeedItem]:
            del kwargs
            return [item]

    messages: list[str] = []

    async def _progress(text: str) -> None:
        messages.append(text)

    researcher = FeedDigestResearcher(
        pipeline=FakePipeline(),
        registry=FakeRegistry(),
        runner=FakeRunner(),
    )

    result = await researcher.collect(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        config=ResearchConfig(budget=ResearchBudget(max_rounds=2, max_actions=4)),
        seed_actions=[ResearchAction(source="GitHub Trending", site="github", command="trending")],
        progress_callback=_progress,
    )

    assert result.items == [item]
    joined = "\n".join(messages)
    assert any("种子信源" in m for m in messages)
    assert "GitHub Trending" in joined
    assert any("第 1 轮检索" in m for m in messages)
    assert any("提取" in m for m in messages)
