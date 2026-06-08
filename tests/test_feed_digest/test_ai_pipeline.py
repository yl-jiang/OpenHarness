"""Tests for feed digest AI pipeline behavior."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from feed_digest.ai_pipeline import (
    FeedDigestAIPipeline,
    _parse_json_list,
    _parse_research_action_entries,
)
from feed_digest.models import FeedItem
from feed_digest.research import OpenCliCommand, RawEvidence, ResearchAction
from openharness.api.client import ApiMessageCompleteEvent, ApiReasoningDeltaEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, ToolUseBlock


class SilentReasoningClient:
    async def stream_message(self, request: Any) -> Any:
        del request
        yield ApiReasoningDeltaEvent(text="我们被要求：输出纯 Markdown，不要 JSON。生成一份简报。")
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(),
        )


class ToolCallingClient:
    def __init__(self, *, tool_name: str, tool_input: dict[str, Any], reasoning: str = "") -> None:
        self._tool_name = tool_name
        self._tool_input = tool_input
        self._reasoning = reasoning
        self._call_count = 0

    async def stream_message(self, request: Any) -> Any:
        del request
        self._call_count += 1
        if self._call_count == 1 and self._reasoning:
            yield ApiReasoningDeltaEvent(text=self._reasoning)
        if self._call_count == 1:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            name=self._tool_name,
                            input=self._tool_input,
                        )
                    ],
                ),
                usage=UsageSnapshot(),
            )
            return
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(
                role="assistant",
                content=[ToolUseBlock(name="done", input={"message": "completed"})],
            ),
            usage=UsageSnapshot(),
        )


def test_ensure_client_preserves_reasoning_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    import openharness.config as oh_config
    import openharness.ui.runtime as ui_runtime

    captured: dict[str, Any] = {}

    class FakeSettings:
        model = "test-model"
        max_tokens = 4096
        provider = "deepseek"

        def merge_cli_overrides(self, *, active_profile: str) -> "FakeSettings":
            captured["active_profile"] = active_profile
            return self

    fake_settings = FakeSettings()
    fake_client = object()

    monkeypatch.setattr(oh_config, "load_settings", lambda: fake_settings)
    def _capture_client(settings: Any) -> Any:
        captured["settings"] = settings
        return fake_client

    monkeypatch.setattr(ui_runtime, "_resolve_api_client_from_settings", _capture_client)

    pipeline = FeedDigestAIPipeline(profile="deepseek")
    pipeline._ensure_client()

    assert captured["active_profile"] == "deepseek"
    assert captured["settings"] is fake_settings
    assert pipeline._settings is fake_settings
    assert pipeline._client is fake_client


@pytest.mark.asyncio
async def test_plan_research_actions_use_agent_loop_output_tool() -> None:
    pipeline = FeedDigestAIPipeline(profile="test")
    pipeline._settings = SimpleNamespace(model="test-model", max_tokens=4096, provider="test")
    pipeline._client = ToolCallingClient(
        tool_name="feed_digest_emit_json",
        tool_input={
            "payload": """
            {
              "done": false,
              "rationale": "Use Hacker News.",
              "actions": [
                {
                  "source": "hackernews",
                  "site": "hackernews",
                  "command": "top",
                  "args": [],
                  "reason": "Find current discussion"
                }
              ]
            }
            """
        },
        reasoning="reasoning before tool call",
    )

    decision = await pipeline.plan_research_actions(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        catalog=[OpenCliCommand(site="hackernews", name="top", strategy="public", browser=False)],
        evidence=[],
        max_actions=3,
    )

    assert len(decision.actions) == 1
    assert decision.actions[0].argv() == ["opencli", "hackernews", "top"]


@pytest.mark.asyncio
async def test_extract_items_use_agent_loop_output_tool() -> None:
    pipeline = FeedDigestAIPipeline(profile="test")
    pipeline._settings = SimpleNamespace(model="test-model", max_tokens=4096, provider="test")
    pipeline._client = ToolCallingClient(
        tool_name="feed_digest_emit_json",
        tool_input={
            "payload": """
            [
              {
                "source": "aibase",
                "title": "Claude Opus 4.8 released",
                "url": "https://example.com/claude-opus-4-8",
                "content": "A model release appeared in the evidence."
              }
            ]
            """
        },
        reasoning="reasoning before tool call",
    )

    items = await pipeline.extract_items_from_evidence(
        [
            RawEvidence(
                source="aibase",
                command="opencli aibase news",
                content="- title: Claude Opus 4.8 released\n  url: https://example.com/claude-opus-4-8",
            )
        ],
        domain="AI & Machine Learning",
        objective="Find important AI news",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].title == "Claude Opus 4.8 released"


@pytest.mark.asyncio
async def test_synthesize_uses_agent_loop_markdown_output_tool() -> None:
    pipeline = FeedDigestAIPipeline(profile="test")
    pipeline._settings = SimpleNamespace(model="test-model", max_tokens=4096, provider="test")
    pipeline._client = ToolCallingClient(
        tool_name="feed_digest_emit_markdown",
        tool_input={
            "markdown": "# AI 热点简报\n\n## 关键趋势\n- Agent adoption keeps growing\n\n### 1️⃣ Please Use AI\n\nA concise synthesis."
        },
        reasoning="reasoning before markdown tool",
    )

    markdown, trends = await pipeline.synthesize(
        [
            FeedItem(
                source="hackernews",
                title="Please Use AI",
                url="https://example.com/use-ai",
                content="Points: 762\nA Hacker News discussion about AI adoption.",
                published_at="2026-05-31T00:00:00Z",
            )
        ],
        title="AI 热点简报",
        domain="AI & Machine Learning",
        max_items=10,
        max_trends=5,
    )

    assert markdown.startswith("# AI 热点简报")
    assert "Please Use AI" in markdown
    assert trends == ["Agent adoption keeps growing"]


@pytest.mark.asyncio
async def test_synthesize_falls_back_when_agent_loop_returns_no_output() -> None:
    pipeline = FeedDigestAIPipeline(profile="test")
    pipeline._settings = SimpleNamespace(model="test-model", max_tokens=4096, provider="test")
    pipeline._client = SilentReasoningClient()

    markdown, trends = await pipeline.synthesize(
        [
            FeedItem(
                source="hackernews",
                title="Please Use AI",
                url="https://example.com/use-ai",
                content="Points: 762\nA Hacker News discussion about AI adoption.",
                published_at="2026-05-31T00:00:00Z",
            )
        ],
        title="AI 热点简报",
        domain="AI & Machine Learning",
        max_items=10,
        max_trends=5,
    )

    assert "Please Use AI" in markdown
    assert trends == []


@pytest.mark.asyncio
async def test_plan_research_actions_parses_llm_json_actions() -> None:
    class PlanningPipeline(FeedDigestAIPipeline):
        async def _complete(self, **kwargs: object) -> str:
            assert "OpenCLI catalog" in str(kwargs["user_prompt"])
            return """
            {
              "done": false,
              "rationale": "Need Hacker News search.",
              "actions": [
                {
                  "source": "hackernews",
                  "site": "hackernews",
                  "command": "search",
                  "args": ["AI agents", "--limit", "5", "-f", "json"],
                  "reason": "Find current discussion"
                }
              ]
            }
            """

    pipeline = PlanningPipeline(profile="test")

    decision = await pipeline.plan_research_actions(
        objective="Find important AI news",
        domain="AI & Machine Learning",
        catalog=[OpenCliCommand(site="hackernews", name="search", strategy="public", browser=False)],
        evidence=[],
        max_actions=3,
    )

    assert not decision.done
    assert decision.actions == [
        ResearchAction(
            source="hackernews",
            site="hackernews",
            command="search",
            args=["AI agents", "--limit", "5", "-f", "json"],
            reason="Find current discussion",
        )
    ]


@pytest.mark.asyncio
async def test_extract_items_from_evidence_parses_llm_json_items() -> None:
    class ExtractionPipeline(FeedDigestAIPipeline):
        async def _complete(self, **kwargs: object) -> str:
            assert "Raw evidence" in str(kwargs["user_prompt"])
            return """
            [
              {
                "source": "hackernews",
                "title": "AI agent launch",
                "url": "https://news.ycombinator.com/item?id=1",
                "content": "A new agent runtime launched.",
                "published_at": "2026-05-31T00:00:00Z",
                "tags": ["agents"],
                "key_facts": ["New runtime"]
              }
            ]
            """

    pipeline = ExtractionPipeline(profile="test")

    items = await pipeline.extract_items_from_evidence(
        [
            RawEvidence(
                source="hackernews",
                command="opencli hackernews search AI -f json",
                content='[{"title":"AI agent launch"}]',
            )
        ],
        domain="AI & Machine Learning",
        objective="Find important AI news",
        max_items=5,
    )

    assert len(items) == 1
    assert items[0].source == "hackernews"
    assert items[0].metadata["evidence_command"] == "opencli hackernews search AI -f json"


@pytest.mark.asyncio
async def test_extract_items_batches_evidence_to_avoid_oversized_json_response() -> None:
    class BatchedExtractionPipeline(FeedDigestAIPipeline):
        def __init__(self) -> None:
            super().__init__(profile="test")
            self.prompts: list[str] = []

        async def _complete(self, **kwargs: object) -> str:
            prompt = str(kwargs["user_prompt"])
            self.prompts.append(prompt)
            if "source=aibase" in prompt:
                return '[{"source":"aibase","title":"AIbase item","url":"https://example.com/aibase"}]'
            return '[{"source":"hackernews","title":"HN item","url":"https://example.com/hn"}]'

    pipeline = BatchedExtractionPipeline()

    items = await pipeline.extract_items_from_evidence(
        [
            RawEvidence(source="aibase", command="opencli aibase news", content="title: AIbase item"),
            RawEvidence(source="hackernews", command="opencli hackernews top", content="title: HN item 1"),
            RawEvidence(source="lobsters", command="opencli lobsters hot", content="title: Lobsters item"),
            RawEvidence(source="producthunt", command="opencli producthunt today", content="title: PH item"),
        ],
        domain="AI & Machine Learning",
        objective="Find important AI news",
        max_items=10,
    )

    assert len(pipeline.prompts) == 2
    assert all("Max items: 24" in prompt for prompt in pipeline.prompts)
    # First batch (aibase + hackernews + lobsters) contains 'source=aibase' → mock returns an aibase item.
    # Second batch (producthunt only) → mock returns 'source=hackernews' which canonicalises to producthunt.
    assert [item.source for item in items] == ["aibase", "producthunt"]


def test_parse_json_list_salvages_complete_items_from_truncated_array() -> None:
    raw = """
    [
      {"source":"aibase","title":"Complete item","url":"https://example.com/1"},
      {"source":"hackernews","title":"Truncated item","url":
    """

    items = _parse_json_list(raw)

    assert items == [{"source": "aibase", "title": "Complete item", "url": "https://example.com/1"}]


def test_parse_research_action_entries_salvages_complete_actions_from_truncated_plan() -> None:
    raw = """
    {
      "done": false,
      "actions": [
        {"source":"aibase","site":"aibase","command":"news","args":[]},
        {"source":"hackernews","site":"hackernews","command":"top","args":[]},
        {"source":"reddit","site":"reddit","command":
    """

    actions = _parse_research_action_entries(raw)

    assert actions == [
        {"source": "aibase", "site": "aibase", "command": "news", "args": []},
        {"source": "hackernews", "site": "hackernews", "command": "top", "args": []},
    ]
