from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, ToolUseBlock
from openharness.tools.base import BaseTool

from ohmo.self_log import (
    OpenHarnessSelfLogAgent,
    SelfLogRecord,
    SelfLogProcessor,
    SelfLogStore,
    SelfLogToolAgent,
    SelfLogToolRegistry,
    extract_self_log_content,
    format_process_result,
    parse_self_log_command,
)


def test_extract_self_log_content_requires_exact_command_name():
    assert extract_self_log_content("/self-logbook 今天不该被拦截") is None
    assert extract_self_log_content("/self-log 今天应该被记录") == "今天应该被记录"
    assert extract_self_log_content("/self-log record 今天也应该被记录") == "今天也应该被记录"


def test_parse_self_log_command_distinguishes_record_report_and_help():
    assert parse_self_log_command("/self-logbook test") is None
    assert parse_self_log_command("今天直接记录") is None
    bare_record = parse_self_log_command("今天直接记录", default_record=True)
    assert bare_record.action == "record"
    assert bare_record.argument == "今天直接记录"
    assert parse_self_log_command("/self-log").action == "help"
    assert parse_self_log_command("/self-log help").action == "help"
    report = parse_self_log_command("/self-log report weekly")
    assert report.action == "report"
    assert report.argument == "weekly"
    record = parse_self_log_command("/self-log 今天继续推进")
    assert record.action == "record"
    assert record.argument == "今天继续推进"
    backfill = parse_self_log_command("/self-log backfill 2026-05-15 昨天补一条")
    assert backfill.action == "backfill"
    assert backfill.argument == "2026-05-15 昨天补一条"


def test_self_log_store_records_entries_under_workspace(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    store = SelfLogStore(workspace)

    entry = store.record(
        "  今天完成了 ohmo self-log 纵切  ",
        channel="feishu",
        sender_id="ou_1",
        chat_id="oc_1",
        message_id="om_1",
    )

    entries = store.list_entries()
    assert store.root == workspace / "self-log"
    assert store.entries_path.stat().st_mode & 0o777 == 0o600
    assert entries == [entry]
    assert entries[0].content == "今天完成了 ohmo self-log 纵切"


class FakeSelfLogAgent:
    def __init__(self, process_result: dict | None = None, report_content: str = "## 周报\n- 有进展"):
        self.process_result = process_result or {
            "corrected_content": "今天完成了 ohmo self-log 纵切。",
            "summary": "完成 ohmo self-log 纵切",
            "tags": "工作,成长",
            "emotion": "积极",
            "emotion_reason": "有明确进展",
            "related_people": "",
            "related_places": "",
            "needs_clarification": False,
            "clarification_reason": "",
            "clarification_questions": [],
            "suggested_profile_updates": [
                {
                    "category": "工作",
                    "entity_type": "项目",
                    "entity_name": "ohmo self-log",
                    "suggested_value": "正在开发的个人记录 app",
                    "confidence": "high",
                },
                {
                    "category": "临时",
                    "entity_type": "地点",
                    "entity_name": "路边",
                    "suggested_value": "一次性地点",
                    "confidence": "low",
                },
            ],
        }
        self.report_content = report_content

    async def process_record(self, raw_content: str, profile_context: str) -> dict:
        return self.process_result

    async def generate_report(self, report_type: str, records: list[dict], profile_context: str) -> str:
        return self.report_content


@pytest.mark.asyncio
async def test_self_log_processor_structures_entries_with_agent_and_profile_updates(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    store = SelfLogStore(workspace)
    entry = store.record("今天完成了 ohmo self-log 纵切")

    result = await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending()

    records = store.list_records()
    updates = store.list_profile_updates()
    assert result.auto_processed == 1
    assert result.pending_confirmations == 0
    assert records[0].entry_id == entry.id
    assert records[0].summary == "完成 ohmo self-log 纵切"
    assert updates[0].entity_name == "ohmo self-log"
    assert [update.entity_name for update in updates] == ["ohmo self-log"]


@pytest.mark.asyncio
async def test_self_log_processor_keeps_uncertain_entries_pending_instead_of_guessing(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    store = SelfLogStore(workspace)
    store.record("今天和小王聊了很久")
    agent = FakeSelfLogAgent(
        {
            "corrected_content": "今天和小王聊了很久",
            "summary": "",
            "tags": "社交",
            "emotion": "中性",
            "emotion_reason": "",
            "related_people": "小王",
            "related_places": "",
            "needs_clarification": True,
            "clarification_reason": "不知道小王是谁",
            "clarification_questions": ["小王是谁？"],
            "suggested_profile_updates": [],
        }
    )

    result = await SelfLogProcessor(store, agent).process_pending()

    assert result.auto_processed == 0
    assert result.pending_confirmations == 1
    assert store.list_records() == []
    pending = store.list_pending_confirmations()
    assert pending[0].clarification_reason == "不知道小王是谁"
    assert pending[0].questions == ["小王是谁？"]


@pytest.mark.asyncio
async def test_self_log_processor_generates_reports_with_agent(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    store = SelfLogStore(workspace)
    store.record("今天完成了报告生成")
    await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending()

    report = await SelfLogProcessor(store, FakeSelfLogAgent(report_content="## 本周概览\n- 完成智能闭环")).generate_report("weekly")

    assert report.report_type == "weekly"
    assert "完成智能闭环" in report.content
    assert store.list_reports()[0] == report


@pytest.mark.asyncio
async def test_self_log_processor_prompts_backfill_when_previous_day_is_missing(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending(process_date="2026-05-16")

    assert result.backfill_date == "2026-05-15"
    assert "2026-05-15" in result.backfill_prompt
    assert store.list_records() == []


@pytest.mark.asyncio
async def test_self_log_processor_marks_backfill_source_and_record_date(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending(
        process_date="2026-05-16",
        backfill_content="昨天补录了一个重要进展",
    )

    records = store.list_records()
    assert result.backfilled is True
    assert result.auto_processed == 1
    assert records[0].date == "2026-05-15"
    assert records[0].source == "补录"


@pytest.mark.asyncio
async def test_self_log_processor_emits_pending_reminder_once_per_five_items(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")
    for index in range(5):
        store.record(f"不确定记录 {index}")
    agent = FakeSelfLogAgent(
        {
            "corrected_content": "",
            "summary": "",
            "tags": "其他",
            "emotion": "中性",
            "needs_clarification": True,
            "clarification_reason": "需要确认",
            "clarification_questions": ["请确认？"],
            "suggested_profile_updates": [],
        }
    )

    first = await SelfLogProcessor(store, agent).process_pending(process_date="2026-05-16")
    second = await SelfLogProcessor(store, agent).process_pending(process_date="2026-05-16")

    assert first.pending_reminder is not None
    assert "5 条待确认" in first.pending_reminder
    assert second.pending_reminder is None


@pytest.mark.asyncio
async def test_self_log_processor_emits_consecutive_missing_day_reminder_once(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")
    store.add_record(
        SelfLogRecord(
            id="old",
            entry_id="old-entry",
            date="2026-05-10",
            raw_content="旧记录",
            corrected_content="旧记录",
            summary="旧记录",
            tags="其他",
            emotion="中性",
            created_at="2026-05-10T00:00:00+00:00",
        )
    )

    first = await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending(process_date="2026-05-14")
    second = await SelfLogProcessor(store, FakeSelfLogAgent()).process_pending(process_date="2026-05-14")

    assert first.consecutive_missing_days == 3
    assert first.missing_day_reminder is not None
    assert "连续 3 天" in first.missing_day_reminder
    assert second.missing_day_reminder is None


def test_format_process_result_includes_backfill_and_reminders(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")
    result = SelfLogProcessor(store, FakeSelfLogAgent()).empty_result(
        backfill_date="2026-05-15",
        backfill_prompt="发现昨天（2026-05-15）没有记录，请补充。",
        pending_reminder="你有 5 条待确认。",
    )

    text = format_process_result(result)

    assert "2026-05-15" in text
    assert "5 条待确认" in text


@pytest.mark.asyncio
async def test_openharness_self_log_agent_uses_openharness_api_client():
    captured: list[ApiMessageRequest] = []

    class FakeApiClient:
        async def stream_message(self, request: ApiMessageRequest):
            captured.append(request)
            yield ApiMessageCompleteEvent(
                message=ConversationMessage.from_user_text(
                    '{"summary":"复用 OpenHarness","needs_clarification":false}'
                ),
                usage=UsageSnapshot(),
            )

    agent = OpenHarnessSelfLogAgent(api_client=FakeApiClient(), model="test-model")

    result = await agent.process_record("今天复用了 OpenHarness", "## 用户背景知识")

    assert result["summary"] == "复用 OpenHarness"
    assert captured
    assert captured[0].model == "test-model"
    assert captured[0].tools == []
    assert isinstance(captured[0].messages[0], ConversationMessage)


def test_self_log_tool_registry_exposes_app_scoped_tools_without_backend_tool_dependency(tmp_path: Path):
    registry = SelfLogToolRegistry(SelfLogStore(tmp_path / ".ohmo-home"), agent_factory=FakeSelfLogAgent)

    schemas = registry.to_api_schema()

    names = {schema["name"] for schema in schemas}
    assert {
        "self_log_record",
        "self_log_process",
        "self_log_backfill",
        "self_log_clarify",
        "self_log_profile_update",
        "self_log_report",
        "self_log_view",
        "self_log_status",
    }.issubset(names)
    assert all(not isinstance(tool, BaseTool) for tool in registry.list_tools())
    assert all("parameters" in schema for schema in schemas)


def test_self_log_tool_schemas_hide_tool_computed_stable_parameters(tmp_path: Path):
    schemas = {
        schema["name"]: schema
        for schema in SelfLogToolRegistry(
            SelfLogStore(tmp_path / ".ohmo-home"),
            agent_factory=FakeSelfLogAgent,
        ).to_api_schema()
    }

    record_properties = schemas["self_log_record"]["parameters"]["properties"]
    process_properties = schemas["self_log_process"]["parameters"]["properties"]
    backfill_properties = schemas["self_log_backfill"]["parameters"]["properties"]

    assert "record_date" not in record_properties
    assert "process_date" not in process_properties
    assert "backfill_content" not in process_properties
    assert "date" not in backfill_properties
    assert schemas["self_log_backfill"]["parameters"]["required"] == ["content"]


@pytest.mark.asyncio
async def test_openharness_self_log_agent_routes_text_with_self_log_tool_schemas():
    captured: list[ApiMessageRequest] = []

    class FakeApiClient:
        async def stream_message(self, request: ApiMessageRequest):
            captured.append(request)
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        ToolUseBlock(
                            id="toolu_record",
                            name="self_log_record",
                            input={"content": "今天完成自然语言工具路由"},
                        )
                    ],
                ),
                usage=UsageSnapshot(),
            )

    agent = OpenHarnessSelfLogAgent(api_client=FakeApiClient(), model="test-model")
    tools = SelfLogToolRegistry(SelfLogStore()).to_api_schema()

    tool_calls = await agent.choose_self_log_tool("帮我记一下今天的进展", tools)

    assert captured[0].tools == tools
    assert tool_calls[0].name == "self_log_record"
    assert tool_calls[0].input["content"] == "今天完成自然语言工具路由"


@pytest.mark.asyncio
async def test_self_log_tool_agent_executes_model_selected_tool(tmp_path: Path):
    workspace = tmp_path / ".ohmo-home"
    store = SelfLogStore(workspace)

    class FakeRouter:
        async def choose_self_log_tool(self, user_text, tools):
            assert any(tool["name"] == "self_log_record" for tool in tools)
            return [
                ToolUseBlock(
                    id="toolu_record",
                    name="self_log_record",
                    input={"content": "今天完成语义工具执行"},
                )
            ]

    result = await SelfLogToolAgent(
        store,
        router=FakeRouter(),
        agent_factory=FakeSelfLogAgent,
    ).run("帮我记一下今天完成语义工具执行")

    assert "刚才的记录已经入库" in result
    assert store.list_entries()[0].content == "今天完成语义工具执行"


@pytest.mark.asyncio
async def test_self_log_record_tool_prompts_backfill_after_recording_when_yesterday_missing(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogToolRegistry(store, agent_factory=FakeSelfLogAgent).execute(
        "self_log_record",
        {"content": "今天一早记录当前状态", "record_date": "2026-05-16"},
    )

    entries = store.list_entries()
    assert entries[0].content == "今天一早记录当前状态"
    assert entries[0].metadata["record_date"] == "2026-05-16"
    assert "发现昨天（2026-05-15）没有记录" in result
    assert "/self-log backfill 2026-05-15" in result


@pytest.mark.asyncio
async def test_self_log_record_tool_does_not_prompt_backfill_when_yesterday_exists(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")
    store.record("昨天已有记录", metadata={"record_date": "2026-05-15"})

    result = await SelfLogToolRegistry(store, agent_factory=FakeSelfLogAgent).execute(
        "self_log_record",
        {"content": "今天正常记录", "record_date": "2026-05-16"},
    )

    assert result == "✅ 刚才的记录已经入库。"


@pytest.mark.asyncio
async def test_self_log_tool_agent_executes_report_tool_after_semantic_routing(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")
    store.record("今天完成了语义报告")

    class FakeRouter:
        async def choose_self_log_tool(self, user_text, tools):
            return [
                ToolUseBlock(
                    id="toolu_report",
                    name="self_log_report",
                    input={"report_type": "weekly"},
                )
            ]

    result = await SelfLogToolAgent(
        store,
        router=FakeRouter(),
        agent_factory=lambda: FakeSelfLogAgent(report_content="## 周报\n- 语义报告已生成"),
    ).run("帮我生成本周复盘")

    assert "语义报告已生成" in result
    assert store.list_reports()[0].report_type == "weekly"


@pytest.mark.asyncio
async def test_self_log_tool_agent_asks_for_backfill_content_when_intent_is_incomplete(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    class FakeRouter:
        async def choose_self_log_tool(self, user_text, tools):
            assert any(tool["name"] == "self_log_clarify" for tool in tools)
            return [
                ToolUseBlock(
                    id="toolu_clarify",
                    name="self_log_clarify",
                    input={
                        "reason": "用户表达了补录意图，但没有提供具体记录内容",
                        "question": "你想补录昨天的什么内容？",
                    },
                )
            ]

    result = await SelfLogToolAgent(
        store,
        router=FakeRouter(),
        agent_factory=FakeSelfLogAgent,
    ).run("我昨天忘记记录了，现在想补录一下")

    assert "你想补录昨天的什么内容" in result
    assert store.list_entries() == []


@pytest.mark.asyncio
async def test_self_log_record_tool_refuses_unclear_record_before_persistence(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogToolRegistry(store, agent_factory=FakeSelfLogAgent).execute(
        "self_log_record",
        {
            "content": "今天和小王聊了很久",
            "needs_clarification": True,
            "clarification_question": "小王是谁？他和你是什么关系？",
            "unclear_fields": ["人物", "人物关系"],
        },
    )

    assert "小王是谁" in result
    assert store.list_entries() == []
    assert store.list_records() == []


@pytest.mark.asyncio
async def test_self_log_record_tool_persists_raw_and_high_level_record(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogToolRegistry(store, agent_factory=FakeSelfLogAgent).execute(
        "self_log_record",
        {
            "content": "今添把slf-log搞定了",
            "record_date": "2026-05-16",
            "corrected_content": "今天把 self-log 搞定了。",
            "summary": "完成 self-log 工具化记录",
            "tags": "工作,成长",
            "emotion": "积极",
            "emotion_reason": "完成关键功能",
            "related_people": "",
            "related_places": "",
        },
    )

    entries = store.list_entries()
    records = store.list_records()
    assert "刚才的记录已经入库" in result
    assert entries[0].content == "今添把slf-log搞定了"
    assert records[0].entry_id == entries[0].id
    assert records[0].corrected_content == "今天把 self-log 搞定了。"
    assert records[0].summary == "完成 self-log 工具化记录"
    assert records[0].tags == "工作,成长"


@pytest.mark.asyncio
async def test_self_log_profile_update_tool_persists_high_value_information(tmp_path: Path):
    store = SelfLogStore(tmp_path / ".ohmo-home")

    result = await SelfLogToolRegistry(store, agent_factory=FakeSelfLogAgent).execute(
        "self_log_profile_update",
        {
            "category": "工作",
            "entity_type": "项目",
            "entity_name": "self-log",
            "suggested_value": "用户正在把 self-log 做成 ohmo 原生 app",
            "confidence": "high",
        },
    )

    updates = store.list_profile_updates()
    assert "资料更新" in result
    assert updates[0].entity_name == "self-log"
    assert updates[0].confidence == "high"
