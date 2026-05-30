"""Integration tests for wolo feed digest."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def run(coro: object) -> object:
    return asyncio.get_event_loop().run_until_complete(coro)


def test_wolo_feed_digest_config_default() -> None:
    from wolo.core.models import WoloConfig

    config = WoloConfig()
    assert config.feed_digest.enabled is True
    assert config.feed_digest.schedule == "30 21 * * *"
    assert config.feed_digest.timezone == "Asia/Shanghai"
    assert config.feed_digest.enable_domains == ["ai_news"]


def test_wolo_feed_digest_archive(tmp_path) -> None:
    from feed_digest.models import FeedDigestResult, SourceStats
    from wolo.core.store import WoloStore

    with patch("wolo.feed_digest.load_config") as mock_cfg, patch(
        "wolo.feed_digest.FeedDigestEngine"
    ) as mock_engine:
        from wolo.core.models import WoloConfig

        cfg = WoloConfig()
        mock_cfg.return_value = cfg

        mock_result = FeedDigestResult(
            date="2024-01-15",
            domain="AI & Machine Learning",
            preset="ai_news",
            period_start="2024-01-14T21:30:00+00:00",
            period_end="2024-01-15T21:30:00+00:00",
            markdown="# AI 热点简报 2024-01-15\n\n## 今日总览\nTest.",
            source_stats=[SourceStats(source="hackernews", fetched=3, selected=1)],
            is_empty=False,
        )
        mock_engine.return_value.run = AsyncMock(return_value=mock_result)

        store = WoloStore(tmp_path)
        store.initialize()

        with patch("wolo.feed_digest.WoloStore", return_value=store):
            from wolo.feed_digest import run_feed_digest

            report = run(run_feed_digest(workspace=tmp_path))

    assert report.report_type == "feed_digest"
    assert report.metadata is not None
    assert report.metadata["preset"] == "ai_news"


def test_wolo_feed_digest_forwards_progress_callback(tmp_path) -> None:
    progress_callback = AsyncMock()

    with patch("wolo.feed_digest.load_config") as mock_cfg, patch(
        "wolo.feed_digest.FeedDigestEngine"
    ) as mock_engine:
        from wolo.core.models import WoloConfig

        cfg = WoloConfig()
        mock_cfg.return_value = cfg
        mock_engine.return_value.run = AsyncMock(
            return_value=SimpleNamespace(
                markdown="# Digest",
                period_start="2024-01-14T21:30:00+00:00",
                period_end="2024-01-15T21:30:00+00:00",
                preset="ai_news",
                domain="AI & Machine Learning",
                date="2024-01-15",
                selected_items=[],
                source_stats=[],
                warnings=[],
                is_empty=False,
            )
        )

        with patch("wolo.feed_digest.WoloStore"):
            from wolo.feed_digest import run_feed_digest

            run(run_feed_digest(workspace=tmp_path, progress_callback=progress_callback))

    mock_engine.return_value.run.assert_awaited_once_with(
        domain_name="ai_news",
        date=None,
        progress_callback=progress_callback,
    )


def test_wolo_feed_digest_not_mix_local(tmp_path) -> None:
    del tmp_path
    from wolo.core.models import WoloConfig

    config = WoloConfig()
    fd_cfg = config.feed_digest
    assert not hasattr(fd_cfg, "include_decisions")
    assert not hasattr(fd_cfg, "include_highlights")
    assert not hasattr(fd_cfg, "include_blockers")


def test_wolo_feed_digest_cron_job_registration(tmp_path) -> None:
    import json

    from wolo.gateway.feed_digest_cron import ensure_feed_digest_job

    ensure_feed_digest_job("wolo", workspace=tmp_path, schedule="30 21 * * *", tz="Asia/Shanghai")
    cron_path = tmp_path / "data" / "cron_jobs.json"
    assert cron_path.exists()
    jobs = json.loads(cron_path.read_text())
    feed_job = next((job for job in jobs if job["name"] == "wolo-feed-digest"), None)
    assert feed_job is not None
    assert feed_job["schedule"] == "30 21 * * *"


def test_wolo_feed_digest_cron_idempotent(tmp_path) -> None:
    import json

    from wolo.gateway.feed_digest_cron import ensure_feed_digest_job

    ensure_feed_digest_job("wolo", workspace=tmp_path)
    ensure_feed_digest_job("wolo", workspace=tmp_path)
    cron_path = tmp_path / "data" / "cron_jobs.json"
    jobs = json.loads(cron_path.read_text())
    assert len([job for job in jobs if job["name"] == "wolo-feed-digest"]) == 1


@pytest.mark.asyncio
async def test_wolo_tool_fetch_digest_uses_registry_progress_callback(tmp_path) -> None:
    from wolo.core.store import WoloStore
    from wolo.tools import WoloToolRegistry

    store = WoloStore(tmp_path)
    progress: list[str] = []

    async def _progress(text: str) -> None:
        progress.append(text)

    async def _fake_run_feed_digest(**kwargs):
        callback = kwargs["progress_callback"]
        assert callback is not None
        await callback("✍️ AI 综合撰写简报，2 条精选内容…（预计 20-40 秒）")
        return SimpleNamespace(content="# Digest", metadata={"is_empty": False})

    registry = WoloToolRegistry(store, progress_callback=_progress)
    with patch("wolo.feed_digest.run_feed_digest", side_effect=_fake_run_feed_digest):
        result = await registry.execute("wolo_fetch_digest", {"preset": "ai_news"})

    assert result == "# Digest"
    assert progress == ["✍️ AI 综合撰写简报，2 条精选内容…（预计 20-40 秒）"]


@pytest.mark.asyncio
async def test_wolo_runner_passes_progress_callback_to_tool_registry(tmp_path, monkeypatch) -> None:
    from wolo.core.store import WoloStore
    from wolo.runner import WoloQueryRunner

    captured: dict[str, object] = {}

    class _FakeSettings:
        model = "test-model"
        max_tokens = 256

        def merge_cli_overrides(self, **kwargs):
            del kwargs
            return self

    class _FakeRegistry:
        def __init__(self, store, *, source_context=None, progress_callback=None):
            del store
            captured["source_context"] = source_context
            captured["progress_callback"] = progress_callback

    class _FakeEngine:
        def __init__(self, **kwargs):
            self.messages = []
            self.tool_metadata = kwargs["tool_metadata"]

        def load_messages(self, messages) -> None:
            del messages

        def set_system_prompt(self, prompt: str) -> None:
            del prompt

        async def submit_message(self, user_message):
            del user_message
            if False:
                yield None

    monkeypatch.setattr("wolo.runner.load_settings", lambda: _FakeSettings())
    monkeypatch.setattr("wolo.runner.WoloToolRegistry", _FakeRegistry)
    monkeypatch.setattr("wolo.runner.build_oh_registry", lambda registry: registry)
    monkeypatch.setattr("wolo.runner.QueryEngine", _FakeEngine)
    monkeypatch.setattr("wolo.runner._build_system_prompt", lambda workspace: "system")
    monkeypatch.setattr("wolo.runner.get_workspace_root", lambda workspace: tmp_path)
    monkeypatch.setattr("wolo.runner.get_skills_dir", lambda workspace: tmp_path / "skills")
    monkeypatch.setattr("wolo.runner._resolve_vision_config", lambda settings: None)

    runner = WoloQueryRunner(WoloStore(tmp_path), api_client=object())

    async def _progress(text: str) -> None:
        del text

    observed: list[tuple[str, str]] = []
    async for event in runner.stream_run(
        "生成今天简报",
        source_context={"channel": "feishu"},
        progress_callback=_progress,
    ):
        observed.append(event)

    assert observed[0] == ("progress", "🤔 正在思考...")
    assert captured["source_context"] == {"channel": "feishu"}
    assert captured["progress_callback"] is _progress


@pytest.mark.asyncio
async def test_wolo_gateway_bridge_publishes_feed_digest_progress(tmp_path) -> None:
    from openharness.channels.bus import InboundMessage, MessageBus
    from wolo.core.store import WoloStore
    from wolo.gateway.bridge import WoloGatewayBridge

    class _FakeRunner:
        def __init__(self, store, *, profile=None):
            assert isinstance(store, WoloStore)
            assert profile == "test-profile"

        async def stream_run(self, user_text, session_key="", *, media=None, source_context=None, progress_callback=None):
            assert user_text == "生成今天简报"
            assert session_key == "feishu:chat-1"
            assert media == []
            assert source_context is not None
            assert source_context["channel"] == "feishu"
            assert source_context["sender_id"] == "user-1"
            assert source_context["chat_id"] == "chat-1"
            assert progress_callback is not None
            await progress_callback("📡 正在采集新闻源…（2 个来源，预计 5-15 秒）")
            yield ("tool_hint", "正在调用 wolo_fetch_digest…")
            yield ("final", "# Digest")

    bus = MessageBus()
    bridge = WoloGatewayBridge(bus=bus, workspace=tmp_path, provider_profile="test-profile")
    message = InboundMessage(channel="feishu", sender_id="user-1", chat_id="chat-1", content="生成今天简报")

    with patch("wolo.gateway.bridge.WoloQueryRunner", _FakeRunner):
        reply = await bridge._handle_record(message, WoloStore(tmp_path), message.content)

    progress_message = await bus.consume_outbound()
    tool_hint_message = await bus.consume_outbound()

    assert reply == "# Digest"
    assert progress_message.content == "📡 正在采集新闻源…（2 个来源，预计 5-15 秒）"
    assert progress_message.metadata == {"_progress": True, "_session_key": "feishu:chat-1"}
    assert tool_hint_message.content == "正在调用 wolo_fetch_digest…"
    assert tool_hint_message.metadata == {
        "_progress": True,
        "_tool_hint": True,
        "_session_key": "feishu:chat-1",
    }
