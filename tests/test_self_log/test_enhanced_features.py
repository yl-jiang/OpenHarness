import pytest
from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

from self_log.store import SelfLogStore
from self_log.models import SelfLogRecord
from self_log.processor import SelfLogProcessor

def _create_record(store, content, summary, tags, date=None, emotion="积极"):
    record = SelfLogRecord(
        id=uuid4().hex[:12],
        entry_id=uuid4().hex[:12],
        date=date or datetime.now(timezone.utc).date().isoformat(),
        raw_content=content,
        corrected_content=content,
        summary=summary,
        tags=tags,
        emotion=emotion,
        created_at=datetime.now(timezone.utc).isoformat()
    )
    store.add_record(record)
    return record

def test_search_records_heuristic(tmp_path: Path):
    workspace = tmp_path / ".self-log"
    store = SelfLogStore(workspace)
    store.initialize()

    _create_record(store, "今天天气不错，去公园跑了步", "公园跑步", "运动,健康", date="2026-05-10")
    _create_record(store, "写了一整天代码，感觉颈椎有点酸", "写代码", "工作,健康", date="2026-05-11")
    _create_record(store, "和家人一起吃了晚餐，很开心", "家人晚餐", "家庭", date="2026-05-12")

    # 1. Search by keyword
    results = store.search_records(query="跑步")
    assert len(results) == 1
    assert "跑步" in results[0].summary

    # 2. Search by multiple tokens (tokens matching across fields)
    results = store.search_records(query="代码 健康")
    assert len(results) >= 1
    assert "代码" in results[0].summary or "健康" in results[0].tags

    # 3. Filter by emotion
    results = store.search_records(emotions=["积极"])
    assert len(results) == 3

    # 4. Filter by date range
    results = store.search_records(start_date="2026-05-11", end_date="2026-05-11")
    assert len(results) == 1
    assert results[0].date == "2026-05-11"

    # 5. Filter by tags
    results = store.search_records(tags=["工作"])
    assert len(results) == 1
    assert "工作" in results[0].tags

def test_search_records_temporal_decay(tmp_path: Path):
    workspace = tmp_path / ".self-log"
    store = SelfLogStore(workspace)
    store.initialize()

    # Old record
    _create_record(store, "我很焦虑", "焦虑记录", "情绪", date="2025-01-01")
    # New record with same keywords
    _create_record(store, "我很焦虑", "焦虑记录", "情绪", date="2026-05-15")

    results = store.search_records(query="我很焦虑")
    assert len(results) == 2
    # Newest should come first due to decay on the old one
    assert results[0].date == "2026-05-15"

@pytest.mark.asyncio
async def test_processor_rag_and_daily_question(tmp_path: Path, monkeypatch):
    workspace = tmp_path / ".self-log"
    store = SelfLogStore(workspace)
    store.initialize()

    # Create a past record for RAG context
    _create_record(store, "我最近在学习 Rust 编程", "学习 Rust", "学习", date="2026-05-10")

    # Mock Agent
    class FakeAgent:
        def __init__(self, *args, **kwargs):
            self.last_context = ""
        
        async def process_record(self, content, context):
            self.last_context = context
            return {
                "corrected_content": content,
                "summary": "Processed",
                "tags": "mock",
                "emotion": "中性"
            }
        
        async def generate_daily_question(self, context):
            return "今天还在学 Rust 吗？"

    fake_agent = FakeAgent()
    processor = SelfLogProcessor(store, agent=fake_agent)

    # 1. Test RAG context injection
    # Use content that will definitely match the past record via jieba tokens
    store.record("今天在学习 Rust 编程")
    await processor.process_pending()
    
    # We check if context was retrieved. If jieba splits differently, we just ensure it's not empty
    assert "Relevant Past Records" in fake_agent.last_context
    assert "学习" in fake_agent.last_context

    # 2. Test Daily Question (no activity on target date)
    # Clear all activity
    store.entries_path.write_text("")
    store.records_path.write_text("")
    
    # We still need some context for the agent to use
    _create_record(store, "我最近在学习 Rust 编程", "学习 Rust", "学习", date="2026-05-10")
    
    result = await processor.process_pending(process_date="2026-05-15")
    assert result.daily_question == "今天还在学 Rust 吗？"

@pytest.mark.asyncio
async def test_tool_visualize_and_export(tmp_path: Path):
    from self_log.tools import SelfLogToolRegistry
    workspace = tmp_path / ".self-log"
    store = SelfLogStore(workspace)
    store.initialize()

    _create_record(store, "心情不错", "心情好", "心情", emotion="积极")
    _create_record(store, "有点累", "累", "心情", emotion="消极")

    registry = SelfLogToolRegistry(store)

    # 1. Test Visualize (emotion distribution)
    res = await registry.execute("self_log_visualize", {"type": "emotion_distribution"})
    assert "积极: █" in res
    assert "消极: █" in res

    # 2. Test Search
    res = await registry.execute("self_log_search", {"query": "不错"})
    assert "找到了 1 条相关记录" in res
    assert "心情好" in res

    # 3. Test Export
    res = await registry.execute("self_log_export", {"format": "markdown"})
    assert "已成功按 markdown 格式导出" in res
    
    # 4. Test Update Record
    record_id = store.list_records()[0].id
    res = await registry.execute("self_log_update_record", {
        "record_id": record_id,
        "summary": "更新后的摘要",
        "tags": "新标签"
    })
    assert "已成功更新记录" in res
    
    updated_record = store.list_records()[0]
    assert updated_record.summary == "更新后的摘要"
    assert updated_record.tags == "新标签"

    # 5. Test Delete Record
    res = await registry.execute("self_log_delete_record", {"record_id": record_id})
    assert "已永久删除记录" in res
    assert len(store.list_records()) == 1 # One remaining from test_tool_visualize_and_export setup

    # Check if file exists
    export_dir = workspace / "exports"
    files = list(export_dir.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text()
    assert "# Self-Log Export" in content
    assert "心情好" in content
