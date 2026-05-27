from pathlib import Path

import pytest

from wolo.processor import WoloProcessor
from wolo.core.store import WoloStore
from wolo.tools import WoloToolRegistry


class _WoloIterationAgent:
    def __init__(self) -> None:
        self.report_context = ""

    async def process_record(self, content, context):
        return {
            "corrected_content": content,
            "summary": "定位到 pytest 失败根因是测试模块命名冲突",
            "tags": "wolo,test,blocker,策略",
            "emotion": "受阻",
            "sample_type": "aware_failure",
            "problem_essence": "pytest 失败不是测试逻辑问题，而是跨包同名模块导致的导入冲突",
            "available_cards": "可以重命名测试文件、按包拆分校验、先跑目标子集",
            "strategy": "先隔离命名冲突，再恢复按包分层验证",
            "next_move": "重命名冲突测试文件并重新跑 solo/wolo pytest",
            "deadline": "2026-05-20",
            "validation_signal": "uv run pytest -q tests/test_solo tests/test_wolo 通过",
        }

    async def extract_artifacts(self, record, raw_content, context):
        return {
            "todos": [
                {
                    "title": "重命名冲突测试文件",
                    "project": "wolo",
                    "priority": "high",
                    "due_date": "2026-05-20",
                }
            ],
            "decisions": [
                {
                    "title": "先解命名冲突再回归全量 pytest",
                    "rationale": "先恢复测试加载稳定性，才能看真实失败",
                    "impact": "solo/wolo 测试链路恢复可验证状态",
                    "project": "wolo",
                }
            ],
            "highlights": [
                {
                    "kind": "blocker",
                    "title": "同名测试模块冲突",
                    "content": "pytest 复用模块缓存，跨包同名 test_standalone 会冲突",
                    "project": "wolo",
                    "tags": "pytest,blocker",
                }
            ],
            "experiments": [
                {
                    "title": "测试命名隔离实验",
                    "hypothesis": "唯一测试文件名能消除 pytest 导入冲突",
                    "problem": "pytest 同名模块冲突",
                    "strategy": "给跨包测试文件加唯一前缀",
                    "next_move": "重命名 test_standalone 文件",
                    "success_signal": "solo/wolo 全量 pytest 通过",
                    "deadline": "2026-05-20",
                    "project": "wolo",
                }
            ],
        }

    async def generate_daily_question(self, context):
        return ""

    async def generate_report(self, report_type, records, profile_context, **kwargs):
        self.report_context = profile_context
        return "report"


@pytest.mark.asyncio
async def test_processor_persists_work_iteration_fields_and_experiments(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    agent = _WoloIterationAgent()
    store.record("今天定位 pytest 失败，发现是跨包同名测试模块冲突")

    result = await WoloProcessor(store, agent=agent).process_pending()

    assert result.auto_processed == 1
    record = store.list_records()[0]
    assert getattr(record, "sample_type") == "aware_failure"
    assert getattr(record, "problem_essence") == "pytest 失败不是测试逻辑问题，而是跨包同名模块导致的导入冲突"
    assert getattr(record, "strategy") == "先隔离命名冲突，再恢复按包分层验证"
    assert getattr(record, "validation_signal") == "uv run pytest -q tests/test_solo tests/test_wolo 通过"

    experiments = store.list_experiments()
    assert len(experiments) == 1
    assert experiments[0].record_id == record.id
    assert experiments[0].title == "测试命名隔离实验"
    assert experiments[0].success_signal == "solo/wolo 全量 pytest 通过"


@pytest.mark.asyncio
async def test_wolo_tools_expose_patterns_experiments_and_playbook(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    processor = WoloProcessor(store, agent=_WoloIterationAgent())
    store.record("今天定位 pytest 失败，发现是跨包同名测试模块冲突")
    await processor.process_pending()

    registry = WoloToolRegistry(store)
    names = {schema["name"] for schema in registry.tool_schemas()}

    assert {"wolo_patterns", "wolo_experiments", "wolo_playbook"} <= names
    assert "aware_failure" in await registry.execute("wolo_patterns", {"days": 30})
    assert "测试命名隔离实验" in await registry.execute("wolo_experiments", {"status": "active"})
    assert "先隔离命名冲突，再恢复按包分层验证" in await registry.execute("wolo_playbook", {"limit": 5})


@pytest.mark.asyncio
async def test_report_context_includes_work_iteration_artifacts(tmp_path: Path):
    store = WoloStore(tmp_path / ".wolo")
    agent = _WoloIterationAgent()
    processor = WoloProcessor(store, agent=agent)
    store.record("今天定位 pytest 失败，发现是跨包同名测试模块冲突")

    await processor.process_pending()
    await processor.generate_report("weekly")

    assert "Work Iteration Artifacts" in agent.report_context
    assert "测试命名隔离实验" in agent.report_context
    assert "重命名冲突测试文件" in agent.report_context
