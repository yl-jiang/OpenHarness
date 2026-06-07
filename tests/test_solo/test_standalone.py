import asyncio
import contextlib
import json
import logging
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from openharness.api.usage import UsageSnapshot
from openharness.channels.bus.events import InboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.engine.stream_events import AssistantTurnComplete, ReasoningDelta, StreamFinished, ToolExecutionCompleted
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_write_tool import SkillWriteInput

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


@pytest.mark.asyncio
async def test_standalone_solo_gateway_does_not_publish_streaming_deltas(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / ".solo"
    bus = MessageBus()

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            pass

        async def stream_run(self, text, session_key="", **kwargs):
            yield ("progress", "🤔 正在思考...")
            yield ("tool_hint", "🛠️ 正在调用 solo_record")
            yield ("reasoning", "内部推理")
            yield ("delta", "response")
            yield ("delta", "已")
            yield ("delta", "记录")
            yield ("final", "已记录 ✅")

    class FakeModelAgent:
        def __init__(self, profile=None):
            pass

    monkeypatch.setattr("solo.gateway.bridge.SoloQueryRunner", FakeToolAgent)
    monkeypatch.setattr("solo.gateway.bridge.OpenHarnessSoloAgent", FakeModelAgent)
    bridge = SoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    messages = []
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今晚加班了",
                metadata={"chat_type": "p2p"},
            )
        )
        while not messages or messages[-1].content != "已记录 ✅":
            messages.append(await asyncio.wait_for(bus.consume_outbound(), timeout=1.0))
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert [msg.content for msg in messages] == [
        "🤔 正在思考...",
        "🛠️ 正在调用 solo_record",
        "已记录 ✅",
    ]


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
    from solo.prompts import TOOL_ROUTER_PROMPT

    assert "提醒" in TOOL_ROUTER_PROMPT
    assert "solo_remind" in TOOL_ROUTER_PROMPT


def test_solo_command_prefix_supports_llm_usage():
    from solo.commands import extract_solo_content, parse_solo_command, solo_help_text

    assert extract_solo_content("/solo record 今天补一条记录") == "今天补一条记录"

    command = parse_solo_command("/solo llm-usage")
    assert command is not None
    assert command.action == "llm_usage"

    help_text = solo_help_text()
    assert "/solo llm-usage" in help_text
    assert "个人记录" in help_text
    assert "/solo backfill" in help_text


def test_solo_cli_help_describes_core_commands():
    from solo.cli import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "独立的个人记录应用" in result.output
    assert "写入一条原始日常记录" in result.output
    assert "整理待处理记录并生成结构化内容" in result.output


@pytest.mark.asyncio
async def test_standalone_solo_gateway_slash_command_reports_llm_usage(tmp_path: Path, monkeypatch):
    workspace = tmp_path / ".solo"
    store = SoloStore(workspace)
    store.record_llm_call("gpt-5", input_tokens=120, output_tokens=48)
    store.record_llm_call("gpt-5", input_tokens=80, output_tokens=32)
    store.record_llm_call("claude-sonnet-4.5", input_tokens=64, output_tokens=20)
    bus = MessageBus()

    class FailRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError("query runner should not be used for /solo llm-usage")

    monkeypatch.setattr("solo.gateway.bridge.SoloQueryRunner", FailRunner)
    bridge = SoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="/solo llm-usage",
                metadata={"chat_type": "p2p"},
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert "solo LLM 调用累计 3 次" in outbound.content
    assert "输入 token 累计 264，输出 token 累计 100" in outbound.content
    assert "- gpt-5: 2 次，输入 200，输出 80" in outbound.content
    assert "- claude-sonnet-4.5: 1 次，输入 64，输出 20" in outbound.content


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
async def test_standalone_solo_gateway_does_not_dedup_same_reply_text_with_different_quotes(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / ".solo"
    bus = MessageBus()
    calls: list[dict[str, object]] = []

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            del store, profile, kw

        async def stream_run(self, text, session_key="", **kwargs):
            calls.append({"text": text, "session_key": session_key, "kwargs": kwargs})
            quoted = kwargs["source_context"]["message_metadata"].get("quoted_context", "")
            yield ("final", f"已处理：{quoted}")

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
                content="是的",
                metadata={
                    "chat_type": "p2p",
                    "parent_id": "parent-1",
                    "quoted_context": "第一条引用",
                },
            )
        )
        first = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="是的",
                metadata={
                    "chat_type": "p2p",
                    "parent_id": "parent-2",
                    "quoted_context": "第二条引用",
                },
            )
        )
        second = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert len(calls) == 2
    assert first.content == "已处理：第一条引用"
    assert second.content == "已处理：第二条引用"


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
    assert {
        "skill_list",
        "skill_load",
        "skill_search",
        "skill_write",
        "skill_patch",
        "skill_delete",
    } <= tool_names


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
async def test_solo_query_runner_trims_long_session_history(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    session_key = "feishu:chat-long"
    save_conversation(
        workspace,
        session_key,
        [ConversationMessage.from_user_text(f"message {i}") for i in range(90)],
        session_id="sid-long",
    )
    captured: dict[str, object] = {}

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            self.messages = list(messages)
            captured["loaded_count"] = len(messages)
            captured["first_loaded"] = messages[0].text

        async def submit_message(self, prompt):
            del prompt
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="收到")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run("今天状态不错", session_key=session_key)

    assert result == "收到"
    assert captured["loaded_count"] == 80
    assert captured["first_loaded"] == "message 10"


@pytest.mark.asyncio
async def test_solo_query_runner_trims_long_session_history_on_turn_boundary(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    session_key = "feishu:chat-turn-boundary"
    messages = [
        ConversationMessage(role="assistant", content=[TextBlock(text="更早的回复")]),
        ConversationMessage.from_user_text("spill user"),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_spill", name="solo_search", input={"query": "spill"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="call_spill", content="spill result")],
        ),
        ConversationMessage(role="assistant", content=[TextBlock(text="spill done")]),
    ]
    for index in range(39):
        messages.extend(
            [
                ConversationMessage.from_user_text(f"turn {index}"),
                ConversationMessage(role="assistant", content=[TextBlock(text=f"reply {index}")]),
            ]
        )
    save_conversation(workspace, session_key, messages, session_id="sid-boundary")
    captured: dict[str, object] = {}

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            self.messages = list(messages)
            captured["loaded_count"] = len(messages)
            captured["first_loaded"] = messages[0].text

        async def submit_message(self, prompt):
            del prompt
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="收到")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run("今天状态不错", session_key=session_key)

    assert result == "收到"
    assert captured["loaded_count"] == 78
    assert captured["first_loaded"] == "turn 0"


@pytest.mark.asyncio
async def test_solo_query_runner_prefers_final_text_after_record_tool(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            del messages

        async def submit_message(self, prompt):
            del prompt
            yield ToolExecutionCompleted(
                tool_name="solo_record",
                output="收到～已记下这条。record_id=abc123",
                is_error=False,
            )
            yield AssistantTurnComplete(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="没消息就是好消息，这条先帮你记下来了。")],
                ),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run("今天P1000群里没消息")

    assert result == "没消息就是好消息，这条先帮你记下来了。"


@pytest.mark.asyncio
async def test_solo_query_runner_skips_retry_after_abnormal_stream_finish(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    calls = 0

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            nonlocal calls
            calls += 1
            self.messages.append(
                prompt if isinstance(prompt, ConversationMessage) else ConversationMessage.from_user_text(prompt)
            )
            yield ReasoningDelta(text="thinking")
            yield StreamFinished(reason="auto_continue_exhausted")

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run("今天状态不错", session_key="feishu:chat-retry")

    assert calls == 1
    assert result == "抱歉，模型思考后未能产生有效输出，请稍后重试。"


@pytest.mark.asyncio
async def test_solo_query_runner_abnormal_termination_avoids_leaking_tool_chain(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    session_key = "feishu:chat-abnormal"
    save_conversation(
        workspace,
        session_key,
        [
            ConversationMessage.from_user_text("上一条正常消息"),
            ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常回复")]),
        ],
        session_id="sid-abnormal",
    )

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            user_message = (
                prompt if isinstance(prompt, ConversationMessage) else ConversationMessage.from_user_text(prompt)
            )
            self.messages.extend(
                [
                    user_message,
                    ConversationMessage(
                        role="assistant",
                        content=[ToolUseBlock(id="call_record", name="solo_record", input={"content": user_message.text})],
                    ),
                    ConversationMessage(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id="call_record",
                                content="收到～已记下这条。record_id=abc123",
                            )
                        ],
                    ),
                    ConversationMessage(
                        role="assistant",
                        content=[ToolUseBlock(id="call_read", name="skill_load", input={"name": "read"})],
                    ),
                    ConversationMessage(
                        role="user",
                        content=[
                            ToolResultBlock(
                                tool_use_id="call_read",
                                content='<skill_content name="read">',
                            )
                        ],
                    ),
                ]
            )
            yield ToolExecutionCompleted(
                tool_name="solo_record",
                output="收到～已记下这条。record_id=abc123",
                is_error=False,
            )
            yield ToolExecutionCompleted(
                tool_name="skill_load",
                output='<skill_content name="read">',
                is_error=False,
            )
            yield StreamFinished(reason="max_turns_exceeded")

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run(
        "今天下午将近6点到家",
        session_key=session_key,
    )

    assert result == "收到～已记下这条。record_id=abc123"
    assert "<skill_content" not in result

    from solo.core.session import load_conversation

    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == "sid-abnormal"
    assert [message.text for message in messages] == ["上一条正常消息", "上一条正常回复"]


@pytest.mark.asyncio
async def test_solo_query_runner_treats_quoted_message_as_reference_only(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    captured: dict[str, object] = {}
    search_queries: list[str] = []

    def fake_search_records(query: str, limit: int = 5):
        del limit
        search_queries.append(query)
        return []

    monkeypatch.setattr(store, "search_records", fake_search_records)

    class FakeQueryEngine:
        def __init__(self, **kwargs):
            self.messages: list[ConversationMessage] = []
            self.tool_metadata = kwargs["tool_metadata"]

        def set_system_prompt(self, prompt: str):
            del prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            captured["prompt"] = prompt.text if isinstance(prompt, ConversationMessage) else prompt
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="收到")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("solo.runner.QueryEngine", FakeQueryEngine)

    result = await SoloQueryRunner(store, api_client=object()).run(
        "你说我昨天不舒服没食欲,我昨天什么时候说的?",
        source_context={
            "message_metadata": {
                "parent_id": "parent-1",
                "quoted_context": "已经补充上了，昨天不舒服所以今晚红烧肉没吃。",
                "quoted_message": {
                    "message_id": "parent-1",
                    "role": "assistant",
                    "sender_label": "OpenHarness Assistant",
                    "sent_at": "2026-06-06T20:12:15+08:00",
                    "msg_type": "text",
                    "content": "已经补充上了，昨天不舒服所以今晚红烧肉没吃。",
                },
            }
        },
    )

    assert result == "收到"
    assert search_queries == ["你说我昨天不舒服没食欲,我昨天什么时候说的?"]
    prompt = str(captured["prompt"])
    assert "## Fact Discipline" in prompt
    assert "## Reply Context (Reference Only)" in prompt
    assert "Use it only as background context" in prompt
    assert "- role: assistant" in prompt
    assert "- sender: OpenHarness Assistant" in prompt
    assert "- sent_at: 2026-06-06T20:12:15+08:00" in prompt
    assert "- message_type: text" in prompt
    assert "- content:" in prompt
    assert "已经补充上了，昨天不舒服所以今晚红烧肉没吃。" in prompt
    assert "## Current User Message" in prompt
    assert "你说我昨天不舒服没食欲,我昨天什么时候说的?" in prompt


@pytest.mark.asyncio
async def test_solo_update_record_allows_same_turn_supplement(tmp_path: Path):
    from solo.tools import SoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    registry = SoloToolRegistry(store)

    created = await registry._handle_record(
        {
            "content": "今晚小朋友家人做了红烧肉，我没吃。",
            "corrected_content": "今天晚饭家人做了红烧肉，我没吃。",
            "summary": "晚饭家人做了红烧肉，自己没吃",
            "tags": "生活, 家庭, 饮食, 家人",
            "emotion": "中性",
        }
    )

    record_id = str(created["record_id"])
    update = await registry._handle_update_record(
        {
            "record_id": record_id,
            "corrected_content": "今天晚饭家人做了红烧肉，因为昨天不舒服没食欲，没吃。",
            "tags": "生活, 家庭, 饮食, 家人, 健康, 不舒服",
            "emotion_reason": "昨天不舒服导致没食欲",
        }
    )

    assert update["ok"] is True
    record = store.get_record(record_id)
    assert record is not None
    assert record.corrected_content == "今天晚饭家人做了红烧肉，因为昨天不舒服没食欲，没吃。"
    assert record.tags == "生活, 家庭, 饮食, 家人, 健康, 不舒服"
    assert record.emotion_reason == "昨天不舒服导致没食欲"


@pytest.mark.asyncio
async def test_solo_skill_write_writes_workspace_local_skills(tmp_path: Path):
    from solo.tools import SoloToolRegistry, build_oh_registry

    workspace = initialize_workspace(tmp_path / ".solo")
    registry = build_oh_registry(SoloToolRegistry(SoloStore(workspace)))
    skill_tool = registry.get("skill_write")
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
        SkillWriteInput(name="evening-wrap", content=content),
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


def test_solo_load_conversation_heals_incomplete_trailing_tool_turn(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".solo")
    session_key = "feishu:chat-corrupt"
    session_id = "sid-corrupt"
    save_conversation(
        workspace,
        session_key,
        [
            ConversationMessage.from_user_text("上一条正常消息"),
            ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常回复")]),
        ],
        session_id=session_id,
    )

    corrupted_messages = [
        ConversationMessage.from_user_text("上一条正常消息"),
        ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常回复")]),
        ConversationMessage.from_user_text("今天下午将近6点到家"),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_record", name="solo_record", input={"content": "今天下午将近6点到家"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="call_record", content="收到～已记下这条。record_id=abc123")],
        ),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_read", name="skill_load", input={"name": "read"})],
        ),
        ConversationMessage(
            role="user",
            content=[ToolResultBlock(tool_use_id="call_read", content='<skill_content name="read">')],
        ),
    ]
    db_path = workspace / "data" / "store.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE conversations SET messages = ?, message_count = ? WHERE session_key = ?",
        (
            json.dumps([message.model_dump(mode="json") for message in corrupted_messages], ensure_ascii=False),
            len(corrupted_messages),
            session_key,
        ),
    )
    conn.commit()
    conn.close()

    from solo.core.session import load_conversation

    messages, loaded_sid = load_conversation(workspace, session_key)

    assert loaded_sid == session_id
    assert [message.text for message in messages] == ["上一条正常消息", "上一条正常回复"]

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT messages, message_count FROM conversations WHERE session_key = ?",
        (session_key,),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[1] == 2
    assert len(json.loads(row[0])) == 2


@pytest.mark.asyncio
async def test_solo_heartbeat_triggers_runner_and_notifies_recent_channel(tmp_path: Path):
    from datetime import date

    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    store = SoloStore(workspace)
    today = date.today().isoformat()
    store.add_todo(
        SoloTodo(
            id="todo1",
            record_id="record1",
            title="预约体检",
            category="健康",
            due_date=today,
        )
    )
    save_conversation(
        workspace,
        "feishu:chat-1",
        [ConversationMessage.from_user_text("最近用飞书记录")],
        session_id="sid-1",
    )
    bus = MessageBus()
    calls: list[str] = []

    class FakeRunner:
        def __init__(self, store, *, profile=None):
            self.store = store
            self.profile = profile

        async def run(self, text, session_key="", **kwargs):
            calls.append(text)
            return '{"notifications": ["预约体检今日到期，记得安排时间"]}'

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
    assert result.notified is True
    # Runner was called with signal data
    assert calls and "预约体检" in calls[0]
    assert outbound.channel == "feishu"
    assert outbound.chat_id == "chat-1"
    assert "预约体检" in outbound.content


@pytest.mark.asyncio
async def test_solo_heartbeat_suppresses_duplicate_signals_with_persisted_cooldown(tmp_path: Path):
    import json
    from datetime import date

    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    SoloStore(workspace).add_todo(
        SoloTodo(
            id="todo1",
            record_id="record1",
            title="补齐日志",
            category="复盘",
            due_date=date.today().isoformat(),
        )
    )
    save_conversation(workspace, "feishu:ou_user", [ConversationMessage.from_user_text("hi")])
    calls: list[dict[str, object]] = []

    class FakeRunner:
        def __init__(self, store, *, profile=None):
            self.store = store

        async def run(self, text, session_key="", **kwargs):
            del text, session_key
            calls.append(dict(kwargs))
            return '{"notifications": ["请先处理今日待办"]}'

    bus = MessageBus()
    service = SoloHeartbeatService(
        bus=bus,
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=["feishu"],
        runner_factory=FakeRunner,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
        max_daily_pushes=10,
    )

    first = await service.trigger_once()
    second = await service.trigger_once()

    assert first.notified is True
    assert second.executed is False
    assert second.reason == "all_acked"
    assert len(calls) == 1
    assert calls[0]["allow_tools"] is False
    assert calls[0]["include_similar_context"] is False
    assert calls[0]["use_session_history"] is False
    assert calls[0]["persist_session"] is False

    state_path = workspace / "data" / "heartbeat_state.json"
    assert state_path.exists()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["acks"]
    assert state["push_history"]
    assert state["pushes_today"] >= 1

    service_reloaded = SoloHeartbeatService(
        bus=bus,
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=["feishu"],
        runner_factory=FakeRunner,
        quiet_hours_start="00:00",
        quiet_hours_end="00:00",
        max_daily_pushes=10,
    )
    third = await service_reloaded.trigger_once()
    assert third.executed is False
    assert third.reason == "all_acked"
    assert len(calls) == 1


def test_solo_heartbeat_failed_cron_jobs_uses_entry_name(tmp_path: Path):
    import json
    from datetime import datetime, timezone

    from solo.gateway.heartbeat import SoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".solo")
    history_path = workspace / "data" / "cron_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "name": "solo-todo-reminder",
                "status": "failed",
                "ended_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    service = SoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=[],
    )
    assert service._check_failed_cron_jobs() == ["solo-todo-reminder"]


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
    from datetime import datetime, timezone

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
            created_at=datetime.now(timezone.utc).isoformat(),
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
    assert "pending_confirmation" in agenda
    assert "他说的是谁" in agenda
    assert "检查这个月的睡眠趋势" in agenda


# ── _extract_final_reply_media tests ──


def test_extract_final_reply_media_tilde_path(tmp_path: Path):
    """Tilde paths in agent replies should be expanded and extracted."""
    from solo.runner import _extract_final_reply_media

    img = tmp_path / "test.jpeg"
    img.write_bytes(b"\xff\xd8dummy")
    # Simulate agent reply using ~/ path
    tilde_path = str(img).replace(str(Path.home()), "~")
    reply = f"这是图片路径：`{tilde_path}`"
    result = _extract_final_reply_media(reply, set())
    assert len(result) == 1
    assert result[0] == str(img)


def test_extract_final_reply_media_absolute_path(tmp_path: Path):
    """Absolute paths in replies should still work."""
    from solo.runner import _extract_final_reply_media

    img = tmp_path / "photo.png"
    img.write_bytes(b"\x89PNGdummy")
    reply = f"文件路径在：{img}"
    result = _extract_final_reply_media(reply, set())
    assert len(result) == 1
    assert result[0] == str(img)


def test_extract_final_reply_media_skips_already_emitted(tmp_path: Path):
    """Paths already in emitted_media should not be returned again."""
    from solo.runner import _extract_final_reply_media

    img = tmp_path / "dup.jpg"
    img.write_bytes(b"\xff\xd8dummy")
    reply = f"path: {img}"
    result = _extract_final_reply_media(reply, {str(img)})
    assert result == []


def test_extract_final_reply_media_nonexistent_path():
    """Paths that don't exist on disk should be silently skipped."""
    from solo.runner import _extract_final_reply_media

    reply = "path: /nonexistent/dir/image.png"
    result = _extract_final_reply_media(reply, set())
    assert result == []
