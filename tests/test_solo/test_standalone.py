import asyncio
import contextlib
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openharness.channels.bus.events import InboundMessage
from openharness.channels.bus.queue import MessageBus

from solo.config import build_channel_manager_config, load_config, save_config
from solo.gateway.bridge import SoloGatewayBridge
from solo.gateway.service import SoloGatewayService
from solo.models import SoloConfig
from solo.store import SoloStore
from solo.workspace import get_config_path, get_data_dir, initialize_workspace, workspace_health


def test_standalone_solo_workspace_and_config_are_independent(tmp_path: Path):
    workspace = tmp_path / ".solo"

    root = initialize_workspace(workspace)
    store = SoloStore(workspace)

    assert root == workspace.resolve()
    assert get_config_path(workspace) == workspace.resolve() / "config.json"
    assert get_data_dir(workspace) == workspace.resolve() / "data"
    assert workspace_health(workspace)["config"] is True
    assert workspace_health(workspace)["attachments_dir"] is True
    assert store.root == workspace.resolve() / "data"


def test_standalone_solo_config_projects_channels(tmp_path: Path):
    workspace = tmp_path / ".solo"
    config = SoloConfig(
        provider_profile="codex",
        enabled_channels=["feishu"],
        channel_configs={"feishu": {"allow_from": ["ou_self"], "app_id": "app"}},
        send_progress=False,
    )

    save_config(config, workspace)
    loaded = load_config(workspace)
    channel_config = build_channel_manager_config(loaded)

    assert loaded.enabled_channels == ["feishu"]
    assert channel_config.channels.feishu.enabled is True
    assert channel_config.channels.feishu.app_id == "app"
    assert channel_config.channels.telegram.enabled is False
    assert channel_config.channels.send_progress is False


@pytest.mark.asyncio
async def test_standalone_solo_gateway_routes_bare_text_to_solo_tools(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / ".solo"
    bus = MessageBus()
    image_path = tmp_path / "photo.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\nsolo")
    calls: list[dict[str, object]] = []

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            self.store = store

        async def stream_run(self, text, session_key="", **kwargs):
            calls.append({"text": text, "kwargs": kwargs})
            self.store.record(text, source_context=kwargs.get("source_context"))
            yield ("final", "已由 standalone solo 入库")

    class FakeModelAgent:
        def __init__(self, profile=None):
            self.profile = profile

    monkeypatch.setattr("solo.gateway.bridge.SoloQueryRunner", FakeToolAgent)
    monkeypatch.setattr("solo.gateway.bridge.OpenHarnessSoloAgent", FakeModelAgent)
    bridge = SoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今天直接记录到独立 solo",
                media=[str(image_path)],
                metadata={"chat_type": "p2p", "message_id": "solo-msg-1"},
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls[0]["text"] == "今天直接记录到独立 solo"
    source_context = calls[0]["kwargs"]["source_context"]
    assert source_context["channel"] == "feishu"
    assert source_context["message_id"] == "solo-msg-1"
    assert source_context["media"] == [str(image_path)]
    assert outbound.content == "已由 standalone solo 入库"
    entry = SoloStore(workspace).list_entries()[0]
    assert entry.content == "今天直接记录到独立 solo"
    assert entry.channel == "feishu"
    assert entry.message_id == "solo-msg-1"
    assert len(entry.attachments) == 1
    assert SoloStore(workspace).resolve_attachment_path(entry.attachments[0]).read_bytes() == image_path.read_bytes()


def test_standalone_solo_gateway_service_uses_standalone_config(tmp_path: Path):
    workspace = tmp_path / ".solo"
    save_config(
        SoloConfig(
            provider_profile="codex",
            enabled_channels=["feishu"],
            channel_configs={"feishu": {"allow_from": ["ou_self"], "app_id": "solo-app"}},
        ),
        workspace,
    )
    old_cwd = Path.cwd()
    try:
        service = SoloGatewayService(tmp_path, workspace)
    finally:
        import os

        os.chdir(old_cwd)

    assert service._config.enabled_channels == ["feishu"]
    assert service._config.channel_configs["feishu"]["app_id"] == "solo-app"


def test_standalone_solo_gateway_run_configures_foreground_logging(tmp_path: Path):
    import logging as stdlib_logging

    from solo.cli import _configure_gateway_logging

    workspace = tmp_path / ".solo"
    save_config(SoloConfig(log_level="DEBUG"), workspace)

    _configure_gateway_logging(workspace)

    root = stdlib_logging.getLogger()
    assert root.level == stdlib_logging.DEBUG
    # Noisy third-party loggers should be silenced
    assert stdlib_logging.getLogger("httpx").level == stdlib_logging.WARNING
    assert stdlib_logging.getLogger("httpcore").level == stdlib_logging.WARNING


@pytest.mark.asyncio
async def test_standalone_solo_gateway_logs_inbound_and_outbound(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    workspace = tmp_path / ".solo"
    bus = MessageBus()

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            pass

        async def stream_run(self, text, session_key="", **kwargs):
            yield ("final", "已记录")

    class FakeModelAgent:
        def __init__(self, profile=None):
            pass

    monkeypatch.setattr("solo.gateway.bridge.SoloQueryRunner", FakeToolAgent)
    monkeypatch.setattr("solo.gateway.bridge.OpenHarnessSoloAgent", FakeModelAgent)
    caplog.set_level(logging.INFO, logger="solo.gateway.bridge")
    bridge = SoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今天直接记录到独立 solo",
                metadata={"chat_type": "p2p"},
            )
        )
        await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    messages = [record.getMessage() for record in caplog.records]
    assert any("solo inbound received channel=feishu" in message for message in messages)
    assert any("solo outbound final channel=feishu" in message for message in messages)


@pytest.mark.asyncio
async def test_standalone_solo_record_tool_persists_traceable_attachments(tmp_path: Path):
    from solo.cli import app
    from solo.tools import SoloToolRegistry, build_oh_registry

    workspace = tmp_path / ".solo"
    image_path = tmp_path / "camera.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\ntraceable")

    store = SoloStore(workspace)
    registry = SoloToolRegistry(
        store,
        source_context={
            "channel": "telegram",
            "sender_id": "user-1",
            "chat_id": "chat-1",
            "message_id": "msg-123",
            "session_key": "telegram:chat-1",
            "received_at": "2026-05-19T17:30:00+08:00",
            "message_metadata": {"message_id": "msg-123", "thread_id": "thread-9"},
            "media": [str(image_path)],
        },
    )

    result = await registry.execute(
        "solo_record",
        {
            "content": "今天把票据照片发给 solo 归档",
            "summary": "票据照片入库",
            "tags": "票据,归档",
            "emotion": "中性",
        },
    )

    entry = store.list_entries()[0]
    record = store.list_records()[0]
    attachment = record.attachments[0]
    stored_path = store.resolve_attachment_path(attachment)
    search = await registry.execute("solo_search", {"query": "票据"})
    detail = await registry.execute("solo_show", {"record_id": record.id})
    tool_names = {tool.name for tool in build_oh_registry(registry).list_tools()}
    runner = CliRunner()
    show = runner.invoke(app, ["show", record.id, "--workspace", str(workspace)])

    assert "record_id=" in result
    assert entry.channel == "telegram"
    assert entry.sender_id == "user-1"
    assert entry.chat_id == "chat-1"
    assert entry.message_id == "msg-123"
    assert entry.metadata["source_message"]["metadata"]["thread_id"] == "thread-9"
    assert len(entry.attachments) == 1
    assert record.attachments == entry.attachments
    assert stored_path.read_bytes() == image_path.read_bytes()
    assert "attachments=1" in search
    assert "camera.png" in search
    assert str(stored_path) in search
    assert f"record_id={record.id}" in detail
    assert "source_message=" in detail
    assert str(stored_path) in detail
    assert show.exit_code == 0
    assert "attachments=1" in show.output
    assert "camera.png" in show.output
    assert str(stored_path) in show.output
    assert "solo_show" in tool_names
    assert "read_file" in tool_names
    assert "image_to_text" in tool_names
