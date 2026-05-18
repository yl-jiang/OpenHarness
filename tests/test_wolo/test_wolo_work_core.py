from pathlib import Path

import pytest

from typer.testing import CliRunner

from openharness.api.client import ApiMessageCompleteEvent, ApiMessageRequest
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, TextBlock

from wolo.agent import (
    OpenHarnessWoloAgent,
    _ARTIFACT_EXTRACTION_SYSTEM_PROMPT,
    _PROCESS_RECORD_SYSTEM_PROMPT,
)
from wolo.models import WoloDecision, WoloHighlight, WoloTodo
from wolo.processor import WoloProcessor
from wolo.store import WoloStore
from wolo.tools import WoloToolRegistry


class _WorkAgent:
    def __init__(self) -> None:
        self.report_context = ""

    async def process_record(self, content, context):
        return {
            "corrected_content": content,
            "summary": "修复 wolo 工作日志结构化",
            "tags": "wolo,code,prompt,tool,blocker",
            "emotion": "受阻",
        }

    async def extract_artifacts(self, record, raw_content, context):
        return {
            "todos": [
                {
                    "title": "补齐 wolo 周报证据链",
                    "project": "wolo",
                    "priority": "high",
                    "due_date": "2026-05-20",
                }
            ],
            "decisions": [
                {
                    "title": "派生 artifact 不塞进主 record",
                    "rationale": "保留历史记录兼容性",
                    "impact": "todos/decisions/highlights 独立演进",
                    "project": "wolo",
                }
            ],
            "highlights": [
                {
                    "kind": "blocker",
                    "title": "pytest 同名模块冲突",
                    "content": "test_standalone.py 名称冲突，需要唯一测试文件名",
                    "project": "wolo",
                    "tags": "test,blocker",
                },
                {
                    "kind": "prompt",
                    "title": "先列边界再 patch",
                    "content": "提示模型先列文件边界，减少无关 diff",
                    "project": "wolo",
                    "tags": "prompt",
                },
            ],
        }

    async def generate_daily_question(self, context):
        return ""

    async def generate_report(self, report_type, records, profile_context):
        self.report_context = profile_context
        return "report"


class _FailingArtifactAgent(_WorkAgent):
    async def extract_artifacts(self, record, raw_content, context):
        raise RuntimeError("artifact extraction temporarily failed")


class _JsonClient:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = outputs
        self.requests: list[ApiMessageRequest] = []

    async def stream_message(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        message = ConversationMessage(role="assistant", content=[TextBlock(text=output)])
        yield ApiMessageCompleteEvent(
            message=message,
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason="end_turn",
        )


def test_record_and_artifact_prompts_have_separate_responsibilities():
    assert "todos" not in _PROCESS_RECORD_SYSTEM_PROMPT
    assert "decisions" not in _PROCESS_RECORD_SYSTEM_PROMPT
    assert "highlights" not in _PROCESS_RECORD_SYSTEM_PROMPT
    assert "todos" in _ARTIFACT_EXTRACTION_SYSTEM_PROMPT
    assert "decisions" in _ARTIFACT_EXTRACTION_SYSTEM_PROMPT
    assert "highlights" in _ARTIFACT_EXTRACTION_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_agent_retries_invalid_json_before_falling_back():
    client = _JsonClient(
        [
            "not json",
            '{"summary":"重试后成功","tags":"wolo","emotion":"完成","needs_clarification":false}',
        ]
    )
    agent = OpenHarnessWoloAgent(
        api_client=client,
        max_json_attempts=2,
        retry_delay_seconds=0,
    )

    result = await agent.process_record("修复 wolo", "context")

    assert result["summary"] == "重试后成功"
    assert len(client.requests) == 2


@pytest.mark.asyncio
async def test_processor_derives_work_artifacts_from_model_output(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    agent = _WorkAgent()
    store.record("今天做 wolo 工作日志结构化，遇到 pytest 同名模块冲突")

    result = await WoloProcessor(store, agent=agent).process_pending()

    assert result.auto_processed == 1
    record = store.list_records()[0]
    todo = store.list_todos()[0]
    decision = store.list_decisions()[0]
    highlights = store.list_highlights()

    assert todo.record_id == record.id
    assert todo.title == "补齐 wolo 周报证据链"
    assert todo.status == "pending"
    assert decision.title == "派生 artifact 不塞进主 record"
    assert decision.record_id == record.id
    assert {item.kind for item in highlights} == {"blocker", "prompt"}
    assert all(item.record_id == record.id for item in highlights)


@pytest.mark.asyncio
async def test_processor_keeps_record_when_artifact_extraction_fails(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    store.record("今天完成主记录，但 artifact 提取失败")

    result = await WoloProcessor(store, agent=_FailingArtifactAgent()).process_pending()

    assert result.auto_processed == 1
    assert store.list_records()[0].summary == "修复 wolo 工作日志结构化"
    assert store.list_todos() == []
    assert store.list_decisions() == []
    assert store.list_highlights() == []


def test_store_searches_and_completes_work_todos(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    todo = WoloTodo(
        id="todo1",
        record_id="record1",
        title="补齐 wolo 周报证据链",
        project="wolo",
        priority="high",
        created_at="2026-05-18T00:00:00+00:00",
    )
    store.add_todo(todo)

    assert store.list_todos(status="pending", project="wolo") == [todo]
    assert store.complete_todo("todo1") is True

    completed = store.list_todos(status="done")[0]
    assert completed.id == "todo1"
    assert completed.completed_at


@pytest.mark.asyncio
async def test_work_tools_query_todos_blockers_decisions_and_lessons(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    store.add_todo(
        WoloTodo(
            id="todo1",
            record_id="record1",
            title="补齐 wolo 周报证据链",
            project="wolo",
            priority="high",
            created_at="2026-05-18T00:00:00+00:00",
        )
    )
    store.add_highlight(
        WoloHighlight(
            id="h1",
            record_id="record1",
            kind="tool",
            title="ruff 先跑新包",
            content="先跑 wolo/tests 的 ruff，再跑仓库级 lint",
            project="wolo",
            tags="tool,lint",
            created_at="2026-05-18T00:00:00+00:00",
        )
    )
    registry = WoloToolRegistry(store)
    names = {schema["name"] for schema in registry.tool_schemas()}

    assert {"wolo_todos", "wolo_done", "wolo_blockers", "wolo_decisions", "wolo_highlights", "wolo_work_query"} <= names
    assert "补齐 wolo 周报证据链" in await registry.execute("wolo_todos", {"status": "pending"})
    assert "ruff 先跑新包" in await registry.execute("wolo_highlights", {"kind": "tool"})
    assert "已完成待办" in await registry.execute("wolo_done", {"todo_id": "todo1"})


@pytest.mark.asyncio
async def test_report_context_includes_work_artifact_evidence(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    agent = _WorkAgent()
    store.record("今天做 wolo 工作日志结构化")
    processor = WoloProcessor(store, agent=agent)

    await processor.process_pending()
    await processor.generate_report("weekly")

    assert "Work Artifacts" in agent.report_context
    assert "补齐 wolo 周报证据链" in agent.report_context
    assert "派生 artifact 不塞进主 record" in agent.report_context
    assert "先列边界再 patch" in agent.report_context


def test_cli_queries_work_artifacts(tmp_path: Path):
    from wolo.cli import app

    workspace = tmp_path / ".wolo"
    store = WoloStore(workspace)
    store.add_todo(
        WoloTodo(
            id="todo1",
            record_id="record1",
            title="补齐 wolo 周报证据链",
            project="wolo",
            created_at="2026-05-18T00:00:00+00:00",
        )
    )
    store.add_decision(
        WoloDecision(
            id="decision1",
            record_id="record1",
            title="派生 artifact 独立存储",
            project="wolo",
            created_at="2026-05-18T00:00:00+00:00",
        )
    )
    store.add_highlight(
        WoloHighlight(
            id="h1",
            record_id="record1",
            kind="tool",
            title="ruff 先跑新包",
            content="先跑 wolo/tests 的 ruff",
            project="wolo",
            tags="tool",
            created_at="2026-05-18T00:00:00+00:00",
        )
    )
    runner = CliRunner()

    todos = runner.invoke(app, ["todos", "--workspace", str(workspace)])
    done = runner.invoke(app, ["done", "todo1", "--workspace", str(workspace)])
    decisions = runner.invoke(app, ["decisions", "--workspace", str(workspace)])
    highlights = runner.invoke(app, ["highlights", "--kind", "tool", "--workspace", str(workspace)])

    assert todos.exit_code == 0
    assert "补齐 wolo 周报证据链" in todos.output
    assert done.exit_code == 0
    assert "已完成待办" in done.output
    assert decisions.exit_code == 0
    assert "派生 artifact 独立存储" in decisions.output
    assert highlights.exit_code == 0
    assert "ruff 先跑新包" in highlights.output
