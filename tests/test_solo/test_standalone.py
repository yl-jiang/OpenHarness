import asyncio
import contextlib
import logging
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openharness.api.usage import UsageSnapshot
from openharness.channels.bus.events import InboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.engine.stream_events import AssistantTurnComplete
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_manager_tool import SkillManagerToolInput

from solo.config import build_channel_manager_config, load_config, save_config
from solo.gateway.bridge import SoloGatewayBridge
from solo.gateway.service import SoloGatewayService
from solo.core.models import PendingConfirmation, SoloConfig, SoloTodo
from solo.runner import SoloQueryRunner
from solo.core.session import save_conversation
from solo.core.store import SoloStore
from solo.core.workspace import get_config_path, get_data_dir, get_skills_dir, initialize_workspace, workspace_health


def test_standalone_solo_workspace_and_config_are_independent(tmp_path: Path):
    workspace = tmp_path / ".solo"

    root = initialize_workspace(workspace)
    store = SoloStore(workspace)

    assert root == workspace.resolve()
    assert get_config_path(workspace) == workspace.resolve() / "config.json"
    assert get_data_dir(workspace) == workspace.resolve() / "data"
    assert workspace_health(workspace)["config"] is True
    assert workspace_health(workspace)["attachments_dir"] is True
    assert workspace_health(workspace)["skills_dir"] is True
    assert get_skills_dir(workspace) == workspace.resolve() / "skills"
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


def test_load_config_backfills_missing_fields(tmp_path: Path):
    import json

    workspace = tmp_path / ".solo"
    workspace.mkdir()
    partial = {"provider_profile": "my-profile", "enabled_channels": ["feishu"]}
    (workspace / "config.json").write_text(json.dumps(partial), encoding="utf-8")

    config = load_config(workspace)

    assert config.provider_profile == "my-profile"
    assert config.enabled_channels == ["feishu"]
    assert config.send_progress is True
    assert config.heartbeat.enabled is True
    assert config.log_level == "INFO"

    saved = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    assert "send_progress" in saved
    assert "heartbeat" in saved
    assert "log_level" in saved


def test_load_config_backfills_missing_nested_heartbeat_fields(tmp_path: Path):
    import json

    workspace = tmp_path / ".solo"
    workspace.mkdir()
    partial = {"provider_profile": "codex", "heartbeat": {"enabled": True, "interval_s": 900}}
    (workspace / "config.json").write_text(json.dumps(partial), encoding="utf-8")

    config = load_config(workspace)

    assert config.heartbeat.enabled is True
    assert config.heartbeat.interval_s == 900
    assert config.heartbeat.keep_recent_messages == 8

    saved = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    assert saved["heartbeat"]["keep_recent_messages"] == 8


def test_load_config_does_not_rewrite_complete_config(tmp_path: Path):
    workspace = tmp_path / ".solo"
    workspace.mkdir()
    save_config(SoloConfig(provider_profile="stable"), workspace)
    config_path = workspace / "config.json"
    content_before = config_path.read_text(encoding="utf-8")

    load_config(workspace)

    assert config_path.read_text(encoding="utf-8") == content_before


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


def test_solo_prompt_routes_future_reminders():
    from solo.runner import _SOLO_TOOL_ROUTER_PROMPT

    assert "提醒" in _SOLO_TOOL_ROUTER_PROMPT
    assert "solo_remind" in _SOLO_TOOL_ROUTER_PROMPT


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


def test_standalone_solo_gateway_logging_writes_workspace_log_file(tmp_path: Path):
    import logging as stdlib_logging

    from openharness.utils.log import get_logger, reset_logging
    from solo.cli import _configure_gateway_logging
    from solo.core.workspace import get_logs_dir

    workspace = tmp_path / ".solo"
    save_config(SoloConfig(log_level="INFO"), workspace)

    reset_logging()
    try:
        _configure_gateway_logging(workspace)
        get_logger("solo.gateway.bridge").info("workspace log test", channel="feishu")
        stdlib_logging.shutdown()
        log_path = get_logs_dir(workspace) / "gateway.log"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "workspace log test" in content
    finally:
        reset_logging()
        stdlib_logging.getLogger().handlers.clear()


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
    assert "bash" in tool_names
    assert "read_file" in tool_names
    assert "image_to_text" in tool_names
    assert "skill_manager" in tool_names


@pytest.mark.asyncio
async def test_solo_query_runner_passes_settings_and_autodream_context(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    skill_dir = get_skills_dir(workspace) / "nightly-reflection"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: nightly-reflection\ndescription: Review the day's emotional patterns.\n---\n\n# Nightly Reflection\n",
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            captured["refreshed_system_prompt"] = prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            self.messages.append(
                prompt if isinstance(prompt, ConversationMessage) else ConversationMessage.from_user_text(prompt)
            )
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="已记录")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)
    runner = SoloQueryRunner(store, api_client=object())

    result = await runner.run("今天状态不错", session_key="feishu:chat-1")

    assert result == "已记录"
    assert captured["settings"] is not None
    assert "nightly-reflection" in captured["system_prompt"]
    assert "Review the day's emotional patterns." in captured["system_prompt"]
    tool_metadata = captured["tool_metadata"]
    assert tool_metadata["extra_skill_dirs"] == (str(workspace / "skills"),)
    assert tool_metadata["user_skills_dir"] == str(workspace / "skills")
    assert tool_metadata["skill_registry_cwd"] is None
    assert callable(tool_metadata["system_prompt_refresher"])
    assert tool_metadata["autodream_context"] == {
        "memory_dir": str(workspace / "memory"),
        "session_dir": str(workspace / "sessions"),
        "app_label": "solo personal memory",
        "runner_module": "ohmo",
    }


@pytest.mark.asyncio
async def test_solo_skill_manager_writes_workspace_local_skills(tmp_path: Path):
    from solo.tools import SoloToolRegistry, build_oh_registry

    workspace = initialize_workspace(tmp_path / ".solo")
    registry = build_oh_registry(SoloToolRegistry(SoloStore(workspace)))
    skill_tool = registry.get("skill_manager")
    assert skill_tool is not None

    context = ToolExecutionContext(
        cwd=tmp_path,
        metadata={
            "extra_skill_dirs": (str(workspace / "skills"),),
            "user_skills_dir": str(workspace / "skills"),
            "skill_registry_cwd": None,
        },
    )
    content = "---\nname: evening-wrap\ndescription: Close the day cleanly.\n---\n\n# Evening Wrap\nCapture loose ends.\n"

    result = await skill_tool.execute(
        SkillManagerToolInput(action="write", name="evening-wrap", content=content),
        context,
    )

    assert result.is_error is False
    skill_path = workspace / "skills" / "evening-wrap" / "SKILL.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == content


def test_solo_save_conversation_writes_session_snapshot_for_autodream(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".solo")
    session_key = "feishu:chat-1"
    session_id = "solo-session-1"

    save_conversation(
        workspace,
        session_key,
        [ConversationMessage.from_user_text("hello solo")],
        session_id=session_id,
    )

    # Verify data is stored in SQLite
    from solo.core.session import load_conversation
    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == session_id
    assert len(messages) == 1
    assert messages[0].text == "hello solo"


def test_solo_save_conversation_roundtrip(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".solo")
    session_key = "feishu:chat-2"

    save_conversation(
        workspace,
        session_key,
        [ConversationMessage.from_user_text("first message")],
        session_id="sid-1",
    )
    save_conversation(
        workspace,
        session_key,
        [
            ConversationMessage.from_user_text("first message"),
            ConversationMessage.from_user_text("second message"),
        ],
        session_id="sid-2",
    )

    from solo.core.session import load_conversation
    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == "sid-2"
    assert len(messages) == 2


@pytest.mark.asyncio
async def test_solo_heartbeat_triggers_runner_and_notifies_recent_channel(tmp_path: Path):
    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    store.add_todo(
        SoloTodo(
            id="todo1",
            record_id="record1",
            title="预约体检",
            category="健康",
            due_date="2026-05-21",
        )
    )
    save_conversation(
        workspace,
        "feishu:chat-1",
        [ConversationMessage.from_user_text("最近用飞书记录")],
        session_id="sid-1",
    )
    bus = MessageBus()
    calls: list[tuple[str, str]] = []

    class FakeRunner:
        def __init__(self, store, *, profile=None):
            self.store = store
            self.profile = profile

        async def run(self, text, session_key="", **kwargs):
            calls.append((text, session_key))
            return "建议今天确认体检时间"

    service = SoloHeartbeatService(
        bus=bus,
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=["feishu"],
        runner_factory=FakeRunner,
    )

    result = await service.trigger_once()
    outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

    assert result.executed is True
    assert "预约体检" in calls[0][0]
    assert calls[0][1] == "heartbeat"
    assert outbound.channel == "feishu"
    assert outbound.chat_id == "chat-1"
    assert outbound.content == "建议今天确认体检时间"


@pytest.mark.asyncio
async def test_solo_heartbeat_skips_when_agenda_is_empty(tmp_path: Path):
    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    bus = MessageBus()
    called = False

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, *args, **kwargs):
            nonlocal called
            called = True
            return "unexpected"

    service = SoloHeartbeatService(
        bus=bus,
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=["feishu"],
        runner_factory=FakeRunner,
    )

    result = await service.trigger_once()

    assert result.executed is False
    assert result.reason == "empty"
    assert called is False


def test_solo_heartbeat_cli_status_reflects_config(tmp_path: Path):
    from solo.cli import app

    workspace = initialize_workspace(tmp_path / ".solo")
    save_config(
        SoloConfig(heartbeat={"enabled": True, "interval_s": 600}),
        workspace,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["heartbeat", "status", "--workspace", str(workspace)])

    assert result.exit_code == 0
    assert "enabled=True" in result.output
    assert "interval_s=600" in result.output


def test_solo_heartbeat_agenda_includes_pending_confirmations_and_file_tasks(tmp_path: Path):
    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    store.add_pending_confirmation(
        PendingConfirmation(
            id="pending1",
            entry_id="entry1",
            raw_content="他说下周去检查",
            clarification_reason="指代不清",
            questions=["他说的是谁？"],
            created_at="2026-05-20T00:00:00+00:00",
        )
    )
    (workspace / "HEARTBEAT.md").write_text("- 检查这个月的睡眠趋势\n", encoding="utf-8")

    agenda = SoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=[],
    ).build_agenda()

    assert agenda is not None
    assert "待确认记录" in agenda
    assert "他说的是谁" in agenda
    assert "检查这个月的睡眠趋势" in agenda
