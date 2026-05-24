from pathlib import Path

import pytest

from solo.processor import SoloProcessor
from solo.core.store import SoloStore
from solo.tools import SoloToolRegistry


class _SoloIterationAgent:
    def __init__(self) -> None:
        self.report_context = ""

    async def process_record(self, content, context):
        return {
            "corrected_content": content,
            "summary": "晚饭后把刷视频冲动改成先去洗澡",
            "tags": "习惯,晚间",
            "emotion": "复杂",
            "sample_type": "avoidance_design",
            "trigger_scene": "晚饭后下意识想去客厅拿手机刷短视频",
            "friction_signal": "",
            "awareness_timing": "当场",
            "break_point": "",
            "bridge_action": "先去洗澡再决定要不要娱乐",
            "environment_design": "把手机固定放在书房充电",
            "next_experiment": "连续 7 天晚饭后先洗澡，再决定是否看手机",
        }

    async def extract_artifacts(self, record, raw_content, context):
        return {
            "todos": [
                {
                    "title": "把手机充电器移到书房",
                    "category": "其他",
                    "priority": "medium",
                    "due_date": "2026-05-25",
                }
            ],
            "experiments": [
                {
                    "title": "晚间手机规避实验",
                    "hypothesis": "晚饭后先洗澡能减少短视频冲动",
                    "trigger": "晚饭后想去客厅摸手机",
                    "desired_action": "直接去洗澡",
                    "environment_design": "手机固定放在书房充电",
                    "success_criteria": "连续 7 天晚上 11 点前不刷短视频",
                    "observation_window": "7天",
                }
            ],
        }

    async def generate_daily_question(self, context):
        return ""

    async def generate_report(self, report_type, records, profile_context):
        self.report_context = profile_context
        return "report"


@pytest.mark.asyncio
async def test_processor_persists_iteration_fields_and_experiments(tmp_path: Path):
    store = SoloStore(tmp_path / ".solo")
    agent = _SoloIterationAgent()
    store.record("晚饭后差点又刷视频了，但我先去洗澡了")

    result = await SoloProcessor(store, agent=agent).process_pending()

    assert result.auto_processed == 1
    record = store.list_records()[0]
    assert getattr(record, "sample_type") == "avoidance_design"
    assert getattr(record, "trigger_scene") == "晚饭后下意识想去客厅拿手机刷短视频"
    assert getattr(record, "environment_design") == "把手机固定放在书房充电"
    assert getattr(record, "next_experiment") == "连续 7 天晚饭后先洗澡，再决定是否看手机"

    experiments = store.list_experiments()
    assert len(experiments) == 1
    assert experiments[0].record_id == record.id
    assert experiments[0].title == "晚间手机规避实验"
    assert experiments[0].success_criteria == "连续 7 天晚上 11 点前不刷短视频"


@pytest.mark.asyncio
async def test_solo_tools_expose_patterns_experiments_and_rulebook(tmp_path: Path):
    store = SoloStore(tmp_path / ".solo")
    processor = SoloProcessor(store, agent=_SoloIterationAgent())
    store.record("昨天晚上差点又刷视频，但我改成先去洗澡了")
    await processor.process_pending()

    registry = SoloToolRegistry(store)
    names = {schema["name"] for schema in registry.tool_schemas()}

    assert {"solo_patterns", "solo_experiments", "solo_rulebook"} <= names
    assert "avoidance_design" in await registry.execute("solo_patterns", {"days": 30})
    assert "晚间手机规避实验" in await registry.execute("solo_experiments", {"status": "active"})
    assert "把手机固定放在书房充电" in await registry.execute("solo_rulebook", {"limit": 5})


@pytest.mark.asyncio
async def test_solo_report_context_includes_iteration_artifacts(tmp_path: Path):
    store = SoloStore(tmp_path / ".solo")
    agent = _SoloIterationAgent()
    processor = SoloProcessor(store, agent=agent)
    store.record("晚饭后差点又刷视频了，但我先去洗澡了")

    await processor.process_pending()
    await processor.generate_report("weekly")

    assert "Iteration Artifacts" in agent.report_context
    assert "晚间手机规避实验" in agent.report_context
    assert "把手机充电器移到书房" in agent.report_context
