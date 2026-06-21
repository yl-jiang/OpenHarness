import asyncio
import contextlib
import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from openharness.api.usage import UsageSnapshot
from openharness.channels.bus.events import InboundMessage
from openharness.channels.bus.queue import MessageBus
from openharness.engine.messages import ConversationMessage, TextBlock, ToolResultBlock, ToolUseBlock
from openharness.engine.stream_events import AssistantTurnComplete, StreamFinished, ToolExecutionCompleted
from openharness.tools.base import ToolExecutionContext
from openharness.tools.skill_write_tool import SkillWriteInput

from wolo.config import load_config, save_config
from wolo.core.models import WoloConfig, WoloTodo
from wolo.gateway.bridge import WoloGatewayBridge
from wolo.runner import WoloQueryRunner
from wolo.core.session import save_conversation
from wolo.core.store import WoloStore
from wolo.core.workspace import get_skills_dir, initialize_workspace

def test_wolo_workspace_and_config_are_independent(tmp_path: Path, monkeypatch):
    from wolo.core.models import WoloConfig
    from wolo.core.store import WoloStore
    from wolo.core.workspace import (
        get_config_path,
        get_data_dir,
        get_soul_path,
        get_workspace_root,
        initialize_workspace,
        workspace_health,
    )

    workspace = tmp_path / ".wolo"
    monkeypatch.setenv("WOLO_WORKSPACE", str(workspace))

    root = initialize_workspace()
    store = WoloStore()

    assert root == workspace.resolve()
    assert get_workspace_root() == workspace.resolve()
    assert get_config_path() == workspace.resolve() / "config.json"
    assert get_data_dir() == workspace.resolve() / "data"
    assert workspace_health()["config"] is True
    assert workspace_health()["attachments_dir"] is True
    assert workspace_health()["skills_dir"] is True
    assert store.root == workspace.resolve() / "data"
    assert WoloConfig().provider_profile == "deepseek"
    assert "work log assistant" in get_soul_path().read_text(encoding="utf-8")
    assert get_skills_dir() == workspace.resolve() / "skills"


def test_wolo_load_config_backfills_missing_fields(tmp_path: Path):
    import json

    workspace = tmp_path / ".wolo"
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


def test_wolo_load_config_backfills_missing_nested_heartbeat_fields(tmp_path: Path):
    import json

    workspace = tmp_path / ".wolo"
    workspace.mkdir()
    partial = {"provider_profile": "codex", "heartbeat": {"enabled": True, "interval_s": 900}}
    (workspace / "config.json").write_text(json.dumps(partial), encoding="utf-8")

    config = load_config(workspace)

    assert config.heartbeat.enabled is True
    assert config.heartbeat.interval_s == 900
    assert config.heartbeat.keep_recent_messages == 8

    saved = json.loads((workspace / "config.json").read_text(encoding="utf-8"))
    assert saved["heartbeat"]["keep_recent_messages"] == 8


def test_wolo_load_config_does_not_rewrite_complete_config(tmp_path: Path):
    workspace = tmp_path / ".wolo"
    workspace.mkdir()
    save_config(WoloConfig(provider_profile="stable"), workspace)
    config_path = workspace / "config.json"
    content_before = config_path.read_text(encoding="utf-8")

    load_config(workspace)

    assert config_path.read_text(encoding="utf-8") == content_before


@pytest.mark.asyncio
async def test_wolo_gateway_does_not_publish_streaming_deltas(tmp_path: Path, monkeypatch):
    workspace = tmp_path / ".wolo"
    bus = MessageBus()

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            pass

        async def stream_run(self, text, session_key="", **kwargs):
            yield ("progress", "🤔 正在思考...")
            yield ("tool_hint", "🛠️ 正在调用 wolo_record")
            yield ("reasoning", "内部推理")
            yield ("delta", "response")
            yield ("delta", "已")
            yield ("delta", "记录")
            yield ("final", "已记录 ✅")

    class FakeModelAgent:
        def __init__(self, profile=None):
            pass

    monkeypatch.setattr("wolo.gateway.bridge.WoloQueryRunner", FakeToolAgent)
    monkeypatch.setattr("wolo.gateway.bridge.OpenHarnessWoloAgent", FakeModelAgent)
    bridge = WoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    messages = []
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="今晚处理了故障",
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
        "🛠️ 正在调用 wolo_record",
        "已记录 ✅",
    ]


@pytest.mark.asyncio
async def test_wolo_gateway_does_not_dedup_same_reply_text_with_different_quotes(
    tmp_path: Path,
    monkeypatch,
):
    workspace = tmp_path / ".wolo"
    bus = MessageBus()
    calls: list[dict[str, object]] = []

    class FakeToolAgent:
        def __init__(self, store, *, profile=None, **kw):
            del store, profile, kw

        async def stream_run(self, text, session_key="", **kwargs):
            calls.append({"text": text, "session_key": session_key, "kwargs": kwargs})
            quoted = kwargs["source_context"]["message_metadata"].get("quoted_context", "")
            yield ("final", f"已处理工作：{quoted}")

    class FakeModelAgent:
        def __init__(self, profile=None):
            self.profile = profile

    monkeypatch.setattr("wolo.gateway.bridge.WoloQueryRunner", FakeToolAgent)
    monkeypatch.setattr("wolo.gateway.bridge.OpenHarnessWoloAgent", FakeModelAgent)
    bridge = WoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="收到",
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
                content="收到",
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
    assert first.content == "已处理工作：第一条引用"
    assert second.content == "已处理工作：第二条引用"


def test_wolo_command_prefix_help_and_work_actions():
    from wolo.commands import extract_wolo_content, parse_wolo_command, wolo_help_text

    assert extract_wolo_content("/wolo record fixed the flaky gateway test") == (
        "fixed the flaky gateway test"
    )

    report = parse_wolo_command("/wolo report monthly")
    assert report is not None
    assert report.action == "report"
    assert report.report_type == "monthly"

    usage = parse_wolo_command("/wolo llm-usage")
    assert usage is not None
    assert usage.action == "llm_usage"

    default = parse_wolo_command("记录今天完成 PR review", default_record=True)
    assert default is not None
    assert default.action == "record"

    help_text = wolo_help_text()
    assert "/wolo process" in help_text
    assert "/wolo llm-usage" in help_text
    assert "工作记录" in help_text
    assert "决策" in help_text


def test_wolo_cli_help_describes_core_commands():
    from typer.testing import CliRunner
    from wolo.cli import app

    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "独立的工作记录应用" in result.output
    assert "查看关键决策" in result.output
    assert "对工作沉淀发起综合查询" in result.output


@pytest.mark.asyncio
async def test_standalone_wolo_gateway_slash_command_reports_llm_usage(tmp_path: Path, monkeypatch):
    workspace = tmp_path / ".wolo"
    store = WoloStore(workspace)
    store.record_llm_call("gpt-5", input_tokens=144, output_tokens=60)
    store.record_llm_call("gpt-5", input_tokens=96, output_tokens=36)
    store.record_llm_call("claude-sonnet-4.5", input_tokens=72, output_tokens=24)
    bus = MessageBus()

    class FailRunner:
        def __init__(self, *args, **kwargs):
            raise AssertionError("query runner should not be used for /wolo llm-usage")

    monkeypatch.setattr("wolo.gateway.bridge.WoloQueryRunner", FailRunner)
    bridge = WoloGatewayBridge(bus=bus, workspace=workspace, provider_profile="codex")
    task = asyncio.create_task(bridge.run())
    try:
        await bus.publish_inbound(
            InboundMessage(
                channel="feishu",
                sender_id="ou_user",
                chat_id="ou_user",
                content="/wolo llm-usage",
                metadata={"chat_type": "p2p"},
            )
        )
        outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
    finally:
        bridge.stop()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert "wolo LLM 调用累计 3 次" in outbound.content
    assert "输入 token 累计 312，输出 token 累计 120" in outbound.content
    assert "- gpt-5: 2 次，输入 240，输出 96" in outbound.content
    assert "- claude-sonnet-4.5: 1 次，输入 72，输出 24" in outbound.content


def test_wolo_tool_names_and_descriptions_are_work_focused(tmp_path: Path):
    from wolo.core.store import WoloStore
    from wolo.tools import WoloToolRegistry

    registry = WoloToolRegistry(WoloStore(tmp_path / ".wolo"))
    schemas = registry.tool_schemas()
    names = {schema["name"] for schema in schemas}

    assert "wolo_record" in names
    assert "wolo_report" in names
    assert all(not name.startswith("solo_") for name in names)

    record_schema = next(schema for schema in schemas if schema["name"] == "wolo_record")
    description = record_schema["description"]
    fields = record_schema["parameters"]["properties"]
    assert "work" in description.lower()
    assert "project" in fields["tags"]["description"].lower()
    assert "prompt" in fields["tags"]["description"].lower()
    assert "tool" in fields["tags"]["description"].lower()


def test_wolo_prompts_are_optimized_for_work_logs():
    from wolo.prompts import PROCESS_RECORD_SYSTEM_PROMPT, TOOL_ROUTER_PROMPT, report_system_prompt

    prompt_text = "\n".join(
        [
            TOOL_ROUTER_PROMPT,
            PROCESS_RECORD_SYSTEM_PROMPT,
            report_system_prompt("weekly"),
        ]
    )

    for expected in ("工作", "项目", "会议", "prompt", "tool", "blocker", "提醒", "wolo_remind"):
        assert expected in prompt_text


def test_wolo_readme_documents_standalone_usage():
    readme = Path("wolo/README.md")

    assert readme.exists()
    content = readme.read_text(encoding="utf-8")
    assert "# wolo" in content
    assert "~/.wolo" in content
    assert "uv run wolo --help" in content
    assert "/wolo report weekly" in content
    assert "prompt" in content
    assert "tool" in content


def test_standalone_wolo_gateway_logging_writes_workspace_log_file(tmp_path: Path):
    import logging as stdlib_logging

    from openharness.utils.log import get_logger, reset_logging
    from wolo.cli import _configure_gateway_logging
    from wolo.core.workspace import get_logs_dir

    workspace = tmp_path / ".wolo"
    save_config(WoloConfig(log_level="INFO"), workspace)

    reset_logging()
    try:
        _configure_gateway_logging(workspace)
        get_logger("wolo.gateway.bridge").info("workspace log test", project="openharness")
        stdlib_logging.shutdown()
        log_path = get_logs_dir(workspace) / "gateway.log"
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "workspace log test" in content
    finally:
        reset_logging()
        stdlib_logging.getLogger().handlers.clear()


@pytest.mark.asyncio
async def test_wolo_query_runner_passes_settings_and_autodream_context(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    skill_dir = get_skills_dir(workspace) / "release-retro"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: release-retro\ndescription: Summarize release outcomes and follow-ups.\n---\n\n# Release Retro\n",
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
                message=ConversationMessage(role="assistant", content=[TextBlock(text="已记录工作")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)
    runner = WoloQueryRunner(store, api_client=object())

    result = await runner.run("今天修了一个 flaky test", session_key="feishu:chat-1")

    assert result == "已记录工作"
    assert captured["settings"] is not None
    assert "release-retro" in captured["system_prompt"]
    assert "Summarize release outcomes and follow-ups." in captured["system_prompt"]
    tool_metadata = captured["tool_metadata"]
    assert tool_metadata["extra_skill_dirs"] == (str(workspace / "skills"),)
    assert tool_metadata["user_skills_dir"] == str(workspace / "skills")
    assert tool_metadata["skill_registry_cwd"] is None
    assert callable(tool_metadata["system_prompt_refresher"])
    assert tool_metadata["autodream_context"] == {
        "memory_dir": str(workspace / "memory"),
        "session_dir": str(workspace / "sessions"),
        "app_label": "wolo work memory",
        "runner_module": "ohmo",
    }


@pytest.mark.asyncio
async def test_wolo_skill_write_writes_workspace_local_skills(tmp_path: Path):
    from wolo.tools import WoloToolRegistry, build_oh_registry

    workspace = initialize_workspace(tmp_path / ".wolo")
    registry = build_oh_registry(WoloToolRegistry(WoloStore(workspace)))
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
    content = "---\nname: standup-brief\ndescription: Capture blockers and next actions.\n---\n\n# Standup Brief\nSummarize progress.\n"

    result = await skill_tool.execute(
        SkillWriteInput(name="standup-brief", content=content),
        context,
    )

    assert result.is_error is False
    skill_path = workspace / "skills" / "standup-brief" / "SKILL.md"
    assert skill_path.exists()
    assert skill_path.read_text(encoding="utf-8") == content


def test_wolo_save_conversation_writes_session_snapshot_for_autodream(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".wolo")
    session_key = "feishu:chat-1"
    session_id = "wolo-session-1"

    save_conversation(
        workspace,
        session_key,
        [ConversationMessage.from_user_text("hello wolo")],
        session_id=session_id,
    )

    # Verify data is stored in SQLite
    from wolo.core.session import load_conversation
    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == session_id
    assert len(messages) == 1
    assert messages[0].text == "hello wolo"


def test_wolo_save_conversation_roundtrip(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".wolo")
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

    from wolo.core.session import load_conversation
    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == "sid-2"
    assert len(messages) == 2


def test_wolo_load_conversation_heals_incomplete_trailing_tool_turn(tmp_path: Path):
    workspace = initialize_workspace(tmp_path / ".wolo")
    session_key = "feishu:chat-corrupt"
    session_id = "sid-corrupt"
    save_conversation(
        workspace,
        session_key,
        [
            ConversationMessage.from_user_text("上一条正常工作消息"),
            ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常工作回复")]),
        ],
        session_id=session_id,
    )

    corrupted_messages = [
        ConversationMessage.from_user_text("上一条正常工作消息"),
        ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常工作回复")]),
        ConversationMessage.from_user_text("今天下午将近6点到家"),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_record", name="wolo_record", input={"content": "今天下午将近6点到家"})],
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

    from wolo.core.session import load_conversation

    messages, loaded_sid = load_conversation(workspace, session_key)

    assert loaded_sid == session_id
    assert [message.text for message in messages] == ["上一条正常工作消息", "上一条正常工作回复"]

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
async def test_wolo_query_runner_prefers_final_text_after_record_tool(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)

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
                tool_name="wolo_record",
                output="收到～已记下这条。record_id=abc123",
                is_error=False,
            )
            yield AssistantTurnComplete(
                message=ConversationMessage(
                    role="assistant",
                    content=[TextBlock(text="这个进展挺关键，先帮你放进工作记录里了。")],
                ),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)

    result = await WoloQueryRunner(store, api_client=object()).run("P1000 场地群今天没消息")

    assert result == "这个进展挺关键，先帮你放进工作记录里了。"


@pytest.mark.asyncio
async def test_wolo_query_runner_treats_quoted_message_as_reference_only(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
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
            captured["system_prompt"] = kwargs.get("system_prompt", "")

        def set_system_prompt(self, prompt: str):
            captured["system_prompt"] = prompt

        def load_messages(self, messages):
            self.messages = list(messages)

        async def submit_message(self, prompt):
            captured["prompt"] = prompt.text if isinstance(prompt, ConversationMessage) else prompt
            yield AssistantTurnComplete(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="收到")]),
                usage=UsageSnapshot(),
            )

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)

    result = await WoloQueryRunner(store, api_client=object()).run(
        "你刚才说这个 blocker 已经解决了,具体是哪条记录?",
        source_context={
            "message_metadata": {
                "parent_id": "parent-1",
                "quoted_context": "已经补充上了，这个 blocker 昨晚解决了。",
                "quoted_message": {
                    "message_id": "parent-1",
                    "role": "participant",
                    "sender_label": "Alice",
                    "sent_at": "2026-06-06T19:08:00+08:00",
                    "msg_type": "text",
                    "content": "已经补充上了，这个 blocker 昨晚解决了。",
                },
            }
        },
    )

    assert result == "收到"
    assert search_queries == ["你刚才说这个 blocker 已经解决了,具体是哪条记录?"]
    system_prompt = str(captured["system_prompt"])
    prompt = str(captured["prompt"])
    assert "## Fact Discipline" in system_prompt
    assert "## Reply Context (Reference Only)" in prompt
    assert "Use it only as background context" in prompt
    assert "- role: participant" in prompt
    assert "- sender: Alice" in prompt
    assert "- sent_at: 2026-06-06T19:08:00+08:00" in prompt
    assert "- message_type: text" in prompt
    assert "- content:" in prompt
    assert "已经补充上了，这个 blocker 昨晚解决了。" in prompt
    assert "## Current User Message" in prompt
    assert "你刚才说这个 blocker 已经解决了,具体是哪条记录?" in prompt


@pytest.mark.asyncio
async def test_wolo_query_runner_trims_long_session_history_on_turn_boundary(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    session_key = "feishu:chat-turn-boundary"
    messages = [
        ConversationMessage(role="assistant", content=[TextBlock(text="更早的回复")]),
        ConversationMessage.from_user_text("spill user"),
        ConversationMessage(
            role="assistant",
            content=[ToolUseBlock(id="call_spill", name="wolo_search", input={"query": "spill"})],
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

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)

    result = await WoloQueryRunner(store, api_client=object()).run("今天工作状态不错", session_key=session_key)

    assert result == "收到"
    assert captured["loaded_count"] == 78
    assert captured["first_loaded"] == "turn 0"


@pytest.mark.asyncio
async def test_wolo_query_runner_abnormal_termination_avoids_leaking_tool_chain(tmp_path: Path, monkeypatch):
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    session_key = "feishu:chat-abnormal"
    save_conversation(
        workspace,
        session_key,
        [
            ConversationMessage.from_user_text("上一条正常工作消息"),
            ConversationMessage(role="assistant", content=[TextBlock(text="上一条正常工作回复")]),
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
                        content=[ToolUseBlock(id="call_record", name="wolo_record", input={"content": user_message.text})],
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
                tool_name="wolo_record",
                output="收到～已记下这条。record_id=abc123",
                is_error=False,
            )
            yield ToolExecutionCompleted(
                tool_name="skill_load",
                output='<skill_content name="read">',
                is_error=False,
            )
            yield StreamFinished(reason="max_turns_exceeded")

    monkeypatch.setattr("wolo.runner.QueryEngine", FakeQueryEngine)

    result = await WoloQueryRunner(store, api_client=object()).run(
        "今天下午将近6点到家",
        session_key=session_key,
    )

    assert result == "收到～已记下这条。record_id=abc123"
    assert "<skill_content" not in result

    from wolo.core.session import load_conversation

    messages, loaded_sid = load_conversation(workspace, session_key)
    assert loaded_sid == "sid-abnormal"
    assert [message.text for message in messages] == ["上一条正常工作消息", "上一条正常工作回复"]


@pytest.mark.asyncio
async def test_wolo_heartbeat_trigger_does_not_notify_without_channel_target(tmp_path: Path):
    from wolo.gateway.heartbeat import WoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".wolo")

    WoloStore(workspace).add_todo(
        WoloTodo(
            id="todo1",
            record_id="record1",
            title="整理周报材料",
            project="OpenHarness",
            due_date=date.today().isoformat(),
        )
    )
    calls: list[str] = []

    class FakeRunner:
        def __init__(self, store, *, profile=None):
            self.store = store

        async def run(self, text, session_key="", **kwargs):
            calls.append(text)
            return '{"notifications": ["整理周报材料（今日到期）"]}'

    service = WoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=["feishu"],
        runner_factory=FakeRunner,
    )

    result = await service.trigger_once()

    # Runner was called with signal context
    assert calls and "整理周报材料" in calls[0]
    assert result.executed is True
    # No conversations saved => no notify target => notified is False
    assert result.notified is False
    assert "整理周报材料" in (result.response or "")


@pytest.mark.asyncio
async def test_wolo_heartbeat_suppresses_duplicate_signals_with_persisted_cooldown(tmp_path: Path):
    from wolo.gateway.heartbeat import WoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".wolo")
    WoloStore(workspace).add_todo(
        WoloTodo(
            id="todo1",
            record_id="record1",
            title="补齐日报",
            project="OpenHarness",
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
            return '{"notifications": ["请先完成今日待办"]}'

    bus = MessageBus()
    service = WoloHeartbeatService(
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

    service_reloaded = WoloHeartbeatService(
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


def test_wolo_heartbeat_failed_cron_jobs_uses_entry_name(tmp_path: Path):
    from wolo.gateway.heartbeat import WoloHeartbeatService

    workspace = initialize_workspace(tmp_path / ".wolo")
    history_path = workspace / "data" / "cron_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(
        json.dumps(
            {
                "name": "wolo-todo-reminder",
                "status": "failed",
                "ended_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    service = WoloHeartbeatService(
        bus=MessageBus(),
        workspace=workspace,
        provider_profile="codex",
        enabled_channels=[],
    )
    assert service._check_failed_cron_jobs() == ["wolo-todo-reminder"]


def test_wolo_heartbeat_cli_status_reflects_config(tmp_path: Path):
    from typer.testing import CliRunner
    from wolo.cli import app

    workspace = initialize_workspace(tmp_path / ".wolo")
    save_config(
        WoloConfig(heartbeat={"enabled": True, "interval_s": 900}),
        workspace,
    )
    runner = CliRunner()

    result = runner.invoke(app, ["heartbeat", "status", "--workspace", str(workspace)])

    assert result.exit_code == 0
    assert "enabled=True" in result.output
    assert "interval_s=900" in result.output


@pytest.mark.asyncio
async def test_wolo_update_record_rejects_same_turn_supplement(tmp_path: Path):
    """Layer-2 same-turn guard for wolo: updating a record created earlier in
    the same registry instance is rejected.
    """
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    registry = WoloToolRegistry(store)

    created = await registry._handle_record(
        {
            "content": "今天把网关模块改完了。",
            "corrected_content": "今天把网关模块重构完成。",
            "summary": "重构网关模块",
            "tags": "code, refactor",
            "emotion": "顺利",
        }
    )

    record_id = str(created["record_id"])
    update = await registry._handle_update_record(
        {
            "record_id": record_id,
            "emotion": "积极",
            "emotion_reason": "重构后代码更清晰",
        }
    )

    assert update["ok"] is False
    assert "同轮创建保护" in update["message"]
    assert record_id in update["message"]
    record = store.get_record(record_id)
    assert record is not None
    assert record.emotion == "顺利"
    assert record.emotion_reason == ""


@pytest.mark.asyncio
async def test_wolo_update_record_allows_cross_turn_correction(tmp_path: Path):
    """Cross-turn corrections still work: a new registry (new user turn) can
    update a record created by an earlier turn.
    """
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    creating = WoloToolRegistry(store)
    created = await creating._handle_record(
        {
            "content": "今天评审了方案。",
            "summary": "评审方案",
            "tags": "meeting",
            "emotion": "中性",
        }
    )
    record_id = str(created["record_id"])

    correcting = WoloToolRegistry(store)
    update = await correcting._handle_update_record(
        {
            "record_id": record_id,
            "summary": "评审并通过方案 A",
            "tags": "meeting, decision",
        }
    )
    assert update["ok"] is True
    record = store.get_record(record_id)
    assert record.summary == "评审并通过方案 A"
    assert record.tags == "meeting, decision"


@pytest.mark.asyncio
async def test_wolo_update_record_same_turn_guard_flag_off(tmp_path: Path, monkeypatch):
    """When WOLO_DISABLE_SAME_TURN_UPDATE_GUARD=1 the wolo registry falls
    back to Layer-4: plain updates pass, but subjective fields on a fresh
    record are rejected.
    """
    from wolo.tools import WoloToolRegistry

    monkeypatch.setenv("WOLO_DISABLE_SAME_TURN_UPDATE_GUARD", "1")
    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    registry = WoloToolRegistry(store)

    created = await registry._handle_record(
        {
            "content": "今天和 PM 同步了进度。",
            "summary": "和 PM 同步进度",
            "tags": "meeting",
            "emotion": "中性",
        }
    )
    record_id = str(created["record_id"])

    plain = await registry._handle_update_record(
        {"record_id": record_id, "tags": "meeting, sync"}
    )
    assert plain["ok"] is True

    hallucination = await registry._handle_update_record(
        {
            "record_id": record_id,
            "strategy": "采用方案 A 推进",
            "next_move": "下周发版",
        }
    )
    assert hallucination["ok"] is False
    assert "推断字段保护" in hallucination["message"]


@pytest.mark.asyncio
async def test_wolo_import_records_tracks_all_ids_for_same_turn_guard(tmp_path: Path):
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    registry = WoloToolRegistry(WoloStore(workspace))

    imported = await registry._handle_import_records(
        {
            "records": [
                {"date": "2026-06-01", "content": "周一开周会", "summary": "周会"},
                {"date": "2026-06-02", "content": "周二评审", "summary": "评审"},
            ]
        }
    )
    record_ids = imported["record_ids"]
    assert len(record_ids) == 2
    assert set(record_ids) == registry._created_record_ids

    for record_id in record_ids:
        update = await registry._handle_update_record(
            {"record_id": record_id, "summary": "改了摘要"}
        )
        assert update["ok"] is False
        assert "同轮创建保护" in update["message"]


@pytest.mark.asyncio
async def test_wolo_update_record_noop_surfaces_is_error(tmp_path: Path):
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)
    creating = WoloToolRegistry(store)
    created = await creating._handle_record(
        {"content": "今天写文档。", "summary": "写文档", "tags": "docs", "emotion": "中性"}
    )
    record_id = str(created["record_id"])

    updating = WoloToolRegistry(store)
    first = await updating._handle_update_record(
        {"record_id": record_id, "summary": "写文档"}
    )
    assert first["ok"] is False
    assert first.get("_is_error") is True
    assert first.get("_metadata", {}).get("noop") is True


@pytest.mark.asyncio
async def test_wolo_record_attaches_creation_metadata(tmp_path: Path):
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    registry = WoloToolRegistry(WoloStore(workspace))

    result = await registry._handle_record(
        {
            "content": "今天上线了 v2。",
            "summary": "上线 v2",
            "tags": "release",
            "emotion": "顺利",
        }
    )
    assert result["ok"] is True
    assert "record_id" not in result["message"]
    metadata = result.get("_metadata")
    assert metadata is not None
    assert metadata["app"] == "wolo"
    assert metadata["domain_event"] == "record_created"
    assert metadata["record_ids"] == [result["record_id"]]


@pytest.mark.asyncio
async def test_wolo_cross_turn_inferred_emotion_blocked_by_layer4(tmp_path: Path):
    """Layer-4 must fire independently of Layer-2 to block the wolo mirror
    of the conversation-history pollution scenario: a fresh work record
    created in a prior turn must not accept an inferred emotion / strategy
    / next_move update in the next turn.
    """
    from wolo.tools import WoloToolRegistry

    workspace = initialize_workspace(tmp_path / ".wolo")
    store = WoloStore(workspace)

    turn_one = WoloToolRegistry(store)
    created = await turn_one._handle_record(
        {
            "content": "今天排查了一个线上报错，还没定位根因。",
            "summary": "排查线上报错",
            "tags": "bug, investigate",
            "emotion": "受阻",
        }
    )
    record_id = str(created["record_id"])

    turn_two = WoloToolRegistry(store)
    update = await turn_two._handle_update_record(
        {
            "record_id": record_id,
            "emotion": "顺利",
            "strategy": "从日志入手定位根因",
            "next_move": "明天复现并修复",
        }
    )
    assert update["ok"] is False
    assert "推断字段保护" in update["message"]

    record = store.get_record(record_id)
    assert record is not None
    assert record.emotion == "受阻"
    assert record.strategy == ""
    assert record.next_move == ""
