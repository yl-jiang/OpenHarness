"""Integration tests for solo feed digest."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def run(coro: object) -> object:
    return asyncio.get_event_loop().run_until_complete(coro)


def test_solo_feed_digest_config_default(tmp_path) -> None:
    del tmp_path
    from solo.core.models import SoloConfig

    config = SoloConfig()
    assert config.feed_digest.enabled is True
    assert config.feed_digest.schedule == "30 21 * * *"
    assert config.feed_digest.timezone == "Asia/Shanghai"
    assert config.feed_digest.enable_domains == ["ai_news"]
    assert config.feed_digest.im_push_enabled is True


def test_solo_feed_digest_archive(tmp_path) -> None:
    from feed_digest.models import FeedDigestResult, SourceStats
    from solo.core.store import SoloStore

    with patch("solo.feed_digest.load_config") as mock_cfg, patch(
        "solo.feed_digest.FeedDigestEngine"
    ) as mock_engine:
        from solo.core.models import SoloConfig

        cfg = SoloConfig()
        mock_cfg.return_value = cfg

        mock_result = FeedDigestResult(
            date="2024-01-15",
            domain="AI & Machine Learning",
            preset="ai_news",
            period_start="2024-01-14T21:30:00+00:00",
            period_end="2024-01-15T21:30:00+00:00",
            markdown="# AI 热点简报 2024-01-15\n\n## 今日总览\nTest.",
            source_stats=[SourceStats(source="github", fetched=5, selected=2)],
            is_empty=False,
        )
        mock_engine.return_value.run = AsyncMock(return_value=mock_result)

        store = SoloStore(tmp_path)
        store.initialize()

        with patch("solo.feed_digest.SoloStore", return_value=store):
            from solo.feed_digest import run_feed_digest

            report = run(run_feed_digest(workspace=tmp_path))

    assert report.report_type == "feed_digest"
    assert report.metadata is not None
    assert report.metadata["preset"] == "ai_news"
    assert report.metadata["domain"] == "AI & Machine Learning"
    reports = store.list_reports()
    fd_reports = [report_item for report_item in reports if report_item.report_type == "feed_digest"]
    assert len(fd_reports) == 1


def test_solo_feed_digest_forwards_progress_callback(tmp_path) -> None:
    progress_callback = AsyncMock()

    with patch("solo.feed_digest.load_config") as mock_cfg, patch(
        "solo.feed_digest.FeedDigestEngine"
    ) as mock_engine:
        from solo.core.models import SoloConfig

        cfg = SoloConfig()
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

        with patch("solo.feed_digest.SoloStore"):
            from solo.feed_digest import run_feed_digest

            run(run_feed_digest(workspace=tmp_path, progress_callback=progress_callback))

    mock_engine.return_value.run.assert_awaited_once_with(
        domain_name="ai_news",
        date=None,
        progress_callback=progress_callback,
    )


def test_solo_feed_digest_not_mix_records(tmp_path) -> None:
    del tmp_path
    from solo.core.models import SoloConfig

    config = SoloConfig()
    fd_cfg = config.feed_digest
    assert not hasattr(fd_cfg, "include_records")
    assert not hasattr(fd_cfg, "include_todos")
    assert not hasattr(fd_cfg, "include_decisions")


def test_solo_feed_digest_cron_job_registration(tmp_path) -> None:
    import json

    from solo.gateway.feed_digest_cron import ensure_feed_digest_job

    ensure_feed_digest_job("solo", workspace=tmp_path, schedule="30 21 * * *", tz="Asia/Shanghai")

    cron_path = tmp_path / "data" / "cron_jobs.json"
    assert cron_path.exists()
    jobs = json.loads(cron_path.read_text())
    feed_job = next((job for job in jobs if job["name"] == "solo-feed-digest"), None)
    assert feed_job is not None
    assert feed_job["schedule"] == "30 21 * * *"
    assert feed_job["timezone"] == "Asia/Shanghai"


def test_solo_feed_digest_cron_idempotent(tmp_path) -> None:
    import json

    from solo.gateway.feed_digest_cron import ensure_feed_digest_job

    ensure_feed_digest_job("solo", workspace=tmp_path)
    ensure_feed_digest_job("solo", workspace=tmp_path)

    cron_path = tmp_path / "data" / "cron_jobs.json"
    jobs = json.loads(cron_path.read_text())
    feed_jobs = [job for job in jobs if job["name"] == "solo-feed-digest"]
    assert len(feed_jobs) == 1


def test_solo_feed_digest_disabled_config(tmp_path) -> None:
    del tmp_path
    from feed_digest.config import FeedDigestConfig
    from solo.core.models import SoloConfig

    config = SoloConfig(feed_digest=FeedDigestConfig(enabled=False))
    assert config.feed_digest.enabled is False


@pytest.mark.asyncio
async def test_solo_tool_fetch_digest_runs_in_background_and_pushes_report(tmp_path) -> None:
    from solo.core.store import SoloStore
    from solo.tools import SoloToolRegistry

    store = SoloStore(tmp_path)
    progress: list[str] = []
    unblock = asyncio.Event()
    delivered = asyncio.Event()

    async def _progress(text: str) -> None:
        progress.append(text)
        if text == "# Digest":
            delivered.set()

    async def _fake_run_feed_digest(**kwargs):
        await unblock.wait()
        callback = kwargs["progress_callback"]
        assert callback is not None
        await callback("🔍 AI 评分过滤，共 2 条候选…（预计 20-40 秒）")
        return SimpleNamespace(
            content="# Digest",
            metadata={
                "domain": "AI & Machine Learning",
                "date": "2024-01-15",
                "is_empty": False,
                "selected_count": 1,
            },
        )

    registry = SoloToolRegistry(store, progress_callback=_progress)
    with patch("solo.feed_digest.run_feed_digest", side_effect=_fake_run_feed_digest):
        result = await registry.execute("solo_fetch_digest", {"preset": "ai_news"})
        assert "后台" in result
        assert progress == []

        unblock.set()
        await asyncio.wait_for(delivered.wait(), timeout=1)

    assert progress == ["🔍 AI 评分过滤，共 2 条候选…（预计 20-40 秒）", "# Digest"]


@pytest.mark.asyncio
async def test_solo_runner_passes_progress_callback_to_tool_registry(tmp_path, monkeypatch) -> None:
    from solo.core.store import SoloStore
    from solo.runner import SoloQueryRunner

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

    monkeypatch.setattr("solo.runner.load_settings", lambda: _FakeSettings())
    monkeypatch.setattr("solo.runner.SoloToolRegistry", _FakeRegistry)
    monkeypatch.setattr("solo.runner.build_oh_registry", lambda registry: registry)
    monkeypatch.setattr("solo.runner.QueryEngine", _FakeEngine)
    monkeypatch.setattr("solo.runner._build_system_prompt", lambda workspace: "system")
    monkeypatch.setattr("solo.runner.get_workspace_root", lambda workspace: tmp_path)
    monkeypatch.setattr("solo.runner.get_skills_dir", lambda workspace: tmp_path / "skills")
    monkeypatch.setattr("solo.runner._resolve_vision_config", lambda settings: None)

    runner = SoloQueryRunner(SoloStore(tmp_path), api_client=object())

    async def _progress(text: str) -> None:
        del text

    observed: list[tuple[str, str]] = []
    async for event in runner.stream_run(
        "生成简报",
        source_context={"channel": "feishu"},
        progress_callback=_progress,
    ):
        observed.append(event)

    assert observed[0] == ("progress", "🤔 正在思考...")
    assert captured["source_context"] == {"channel": "feishu"}
    assert captured["progress_callback"] is _progress


@pytest.mark.asyncio
async def test_solo_gateway_bridge_publishes_feed_digest_progress(tmp_path) -> None:
    from openharness.channels.bus import InboundMessage, MessageBus
    from solo.core.store import SoloStore
    from solo.gateway.bridge import SoloGatewayBridge

    class _FakeRunner:
        def __init__(self, store, *, profile=None):
            assert isinstance(store, SoloStore)
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
            yield ("tool_hint", "正在调用 solo_fetch_digest…")
            yield ("final", "# Digest")

    bus = MessageBus()
    bridge = SoloGatewayBridge(bus=bus, workspace=tmp_path, provider_profile="test-profile")
    message = InboundMessage(channel="feishu", sender_id="user-1", chat_id="chat-1", content="生成今天简报")

    with patch("solo.gateway.bridge.SoloQueryRunner", _FakeRunner):
        reply = await bridge._handle_record(message, SoloStore(tmp_path), message.content)

    progress_message = await bus.consume_outbound()
    tool_hint_message = await bus.consume_outbound()

    assert reply == "# Digest"
    assert progress_message.content == "📡 正在采集新闻源…（2 个来源，预计 5-15 秒）"
    assert progress_message.metadata == {"_progress": True, "_session_key": "feishu:chat-1"}
    assert tool_hint_message.content == "正在调用 solo_fetch_digest…"
    assert tool_hint_message.metadata == {
        "_progress": True,
        "_tool_hint": True,
        "_session_key": "feishu:chat-1",
    }
