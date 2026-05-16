import asyncio
import contextlib
import logging
from pathlib import Path

import pytest

from openharness.channels.bus.events import InboundMessage
from openharness.channels.bus.queue import MessageBus

from self_log.config import build_channel_manager_config, load_config, save_config
from self_log.gateway.bridge import SelfLogGatewayBridge
from self_log.gateway.service import SelfLogGatewayService
from self_log.models import SelfLogConfig
from self_log.store import SelfLogStore
from self_log.workspace import get_config_path, get_data_dir, initialize_workspace, workspace_health


def test_standalone_self_log_workspace_and_config_are_independent(tmp_path: Path):
    workspace = tmp_path / ".self-log"

    root = initialize_workspace(workspace)
    store = SelfLogStore(workspace)

    assert root == workspace.resolve()
    assert get_config_path(workspace) == workspace.resolve() / "config.json"
    assert get_data_dir(workspace) == workspace.resolve() / "data"
    assert workspace_health(workspace)["config"] is True
    assert store.root == workspace.resolve() / "data"


def test_standalone_self_log_config_projects_channels(tmp_path: Path):
    workspace = tmp_path / ".self-log"
    config = SelfLogConfig(
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
async def test_standalone_self_log_gateway_routes_bare_text_to_self_log_tools(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / ".self-log"
    bus = MessageBus()
    calls = []

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            self.store = store

        async def run(self, text, session_key=""):
            calls.append(text)
            self.store.record(text)
            return "已由 standalone self-log 入库"

    class FakeModelAgent:
        def __init__(self, profile=None):
            self.profile = profile

    monkeypatch.setattr("self_log.gateway.bridge.SelfLogQueryRunner", FakeToolAgent)
    monkeypatch.setattr("self_log.gateway.bridge.OpenHarnessSelfLogAgent", FakeModelAgent)
    bridge = SelfLogGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今天直接记录到独立 self-log",
                metadata={"chat_type": "p2p"},
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert calls == ["今天直接记录到独立 self-log"]
    assert outbound.content == "已由 standalone self-log 入库"
    assert SelfLogStore(workspace).list_entries()[0].content == "今天直接记录到独立 self-log"


def test_standalone_self_log_gateway_service_uses_standalone_config(tmp_path: Path):
    workspace = tmp_path / ".self-log"
    save_config(
        SelfLogConfig(
            provider_profile="codex",
            enabled_channels=["feishu"],
            channel_configs={"feishu": {"allow_from": ["ou_self"], "app_id": "self-log-app"}},
        ),
        workspace,
    )
    old_cwd = Path.cwd()
    try:
        service = SelfLogGatewayService(tmp_path, workspace)
    finally:
        import os

        os.chdir(old_cwd)

    assert service._config.enabled_channels == ["feishu"]
    assert service._config.channel_configs["feishu"]["app_id"] == "self-log-app"


def test_standalone_self_log_gateway_run_configures_foreground_logging(tmp_path: Path):
    import logging as stdlib_logging

    from self_log.cli import _configure_gateway_logging

    workspace = tmp_path / ".self-log"
    save_config(SelfLogConfig(log_level="DEBUG"), workspace)

    _configure_gateway_logging(workspace)

    root = stdlib_logging.getLogger()
    assert root.level == stdlib_logging.DEBUG
    # Noisy third-party loggers should be silenced
    assert stdlib_logging.getLogger("httpx").level == stdlib_logging.WARNING
    assert stdlib_logging.getLogger("httpcore").level == stdlib_logging.WARNING


@pytest.mark.asyncio
async def test_standalone_self_log_gateway_logs_inbound_and_outbound(
    tmp_path: Path,
    monkeypatch,
    caplog,
):
    workspace = tmp_path / ".self-log"
    bus = MessageBus()

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            pass

        async def run(self, text, session_key=""):
            return "已记录"

    class FakeModelAgent:
        def __init__(self, profile=None):
            pass

    monkeypatch.setattr("self_log.gateway.bridge.SelfLogQueryRunner", FakeToolAgent)
    monkeypatch.setattr("self_log.gateway.bridge.OpenHarnessSelfLogAgent", FakeModelAgent)
    caplog.set_level(logging.INFO, logger="self_log.gateway.bridge")
    bridge = SelfLogGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今天直接记录到独立 self-log",
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
    assert any("self-log inbound received channel=feishu" in message for message in messages)
    assert any("self-log outbound final channel=feishu" in message for message in messages)
