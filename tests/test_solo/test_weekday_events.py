import pytest
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from solo.store import SoloStore
from solo.models import SoloRecord
from solo.processor import SoloProcessor

@pytest.mark.asyncio
async def test_weekday_and_events_processing(tmp_path: Path):
    workspace = tmp_path / ".solo"
    store = SoloStore(workspace)
    store.initialize()

    # Mock Agent
    class FakeAgent:
        async def process_record(self, content, context):
            # Verify context contains temporal info
            assert "Temporal Context" in context
            assert "Record Target Date: 2026-05-10" in context
            assert "Record Day of Week: 星期日" in context
            
            return {
                "corrected_content": content,
                "summary": "Processed",
                "tags": "mock",
                "emotion": "积极",
                "events": "给妈妈买了礼物" # This is a semantic event from user
            }
        
        async def generate_daily_question(self, context):
            return "Mock question"

    fake_agent = FakeAgent()
    processor = SoloProcessor(store, agent=fake_agent)

    # 1. Record an entry on real Mother's Day (May 10, 2026)
    store.record("今天天气很好", metadata={"record_date": "2026-05-10"})
    
    # 2. Process
    result = await processor.process_pending()
    assert result.auto_processed == 1
    
    # 3. Verify record
    records = store.list_records()
    assert len(records) == 1
    record = records[0]
    assert record.date == "2026-05-10"
    assert record.weekday == "星期日"
    # Should contain BOTH the programmatic holiday and semantic event
    assert "母亲节" in record.events
    assert "给妈妈买了礼物" in record.events

@pytest.mark.asyncio
async def test_search_by_weekday_and_events(tmp_path: Path):
    workspace = tmp_path / ".solo"
    store = SoloStore(workspace)
    store.initialize()

    # Create records with weekday and events
    r1 = SoloRecord(
        id=uuid4().hex[:12],
        entry_id=uuid4().hex[:12],
        date="2026-05-17",
        raw_content="Content 1",
        corrected_content="Content 1",
        summary="Summary 1",
        tags="tag1",
        emotion="中性",
        weekday="星期日",
        events="休息日"
    )
    store.add_record(r1)

    r2 = SoloRecord(
        id=uuid4().hex[:12],
        entry_id=uuid4().hex[:12],
        date="2026-05-18",
        raw_content="Content 2",
        corrected_content="Content 2",
        summary="Summary 2",
        tags="tag2",
        emotion="中性",
        weekday="星期一",
        events="开会, 生日"
    )
    store.add_record(r2)

    # Search by weekday
    results = store.search_records(query="星期一")
    assert len(results) >= 1
    assert results[0].weekday == "星期一"

    # Search by event
    results = store.search_records(query="生日")
    assert len(results) >= 1
    assert "生日" in results[0].events

@pytest.mark.asyncio
async def test_metadata_auto_calculation(tmp_path: Path):
    workspace = tmp_path / ".solo"
    store = SoloStore(workspace)
    store.initialize()

    class FakeAgent:
        async def process_record(self, content, context):
            return {
                "corrected_content": content,
                "summary": "Summary",
                "tags": "tag",
                "emotion": "中性",
                "events": ""
            }
        async def generate_daily_question(self, context): return ""

    processor = SoloProcessor(store, agent=FakeAgent())

    # Test record on a holiday weekend in spring
    # 2026-05-01 is Friday (Labor Day)
    # 2026-05-02 is Saturday
    store.record("今天去劳动节聚会", created_at="2026-05-01T10:00:00+00:00", metadata={"record_date": "2026-05-01"})
    store.record("周六休息", created_at="2026-05-02T23:30:00+00:00", metadata={"record_date": "2026-05-02"})
    
    await processor.process_pending()
    
    records = store.list_records()
    r1 = [r for r in records if r.date == "2026-05-01"][0]
    r2 = [r for r in records if r.date == "2026-05-02"][0]
    
    # Verify R1 (2026-05-01, Friday, 10:00)
    assert r1.events == "劳动节"
    assert r1.period == "上午"
    assert r1.season == "春季"
    assert r1.is_weekend is False
    assert r1.content_length == len("今天去劳动节聚会")
    
    # Verify R2 (2026-05-02, Saturday, 23:30)
    assert r2.period == "深夜"
    assert r2.is_weekend is True
    
    # Search by metadata keywords
    results = store.search_records(query="劳动节")
    assert len(results) >= 1
    
    results = store.search_records(query="深夜")
    assert len(results) >= 1
    
    results = store.search_records(query="工作日")
    assert len(results) >= 1

@pytest.mark.asyncio
async def test_semantic_time_extraction(tmp_path: Path):
    workspace = tmp_path / ".solo"
    store = SoloStore(workspace)
    store.initialize()

    class FakeAgent:
        async def process_record(self, content, context):
            if "8点" in content:
                return {
                    "period": "清晨",
                    "corrected_content": content,
                    "summary": "起床",
                    "tags": "生活",
                    "emotion": "积极"
                }
            return {
                "corrected_content": content,
                "summary": "Summary",
                "tags": "tag",
                "emotion": "中性"
            }
        async def generate_daily_question(self, context): return ""

    processor = SoloProcessor(store, agent=FakeAgent())

    # Recorded at 11:00 AM (中午), but content says 8:00 AM (清晨)
    store.record("今天早上我8点左右起床的", created_at="2026-05-17T11:00:00+00:00")
    
    await processor.process_pending()
    
    records = store.list_records()
    assert len(records) == 1
    # Should be "清晨" because of semantic extraction override
    assert records[0].period == "清晨"
    # Should calculate correct weekday even for today
    assert records[0].weekday == "星期日"
