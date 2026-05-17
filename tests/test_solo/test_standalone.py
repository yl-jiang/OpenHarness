import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

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
    calls = []

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            self.store = store

        async def stream_run(self, text, session_key=""):
            calls.append(text)
            self.store.record(text)
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
                metadata={"chat_type": "p2p"},
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls == ["今天直接记录到独立 solo"]
    assert outbound.content == "已由 standalone solo 入库"
    assert SoloStore(workspace).list_entries()[0].content == "今天直接记录到独立 solo"


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

        async def stream_run(self, text, session_key=""):
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
