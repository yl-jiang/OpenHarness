"""Tests for health records: store CRUD, tool handlers, and post-turn backfill."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from solo.core.models import SoloHealthRecord
from solo.core.store import SoloStore
from solo.tools import SoloToolRegistry


def _make_health(
    *,
    subject: str = "self",
    category: str = "symptom",
    item: str = "头疼",
    date: str = "2026-06-20",
    **kwargs,
) -> SoloHealthRecord:
    return SoloHealthRecord(
        id=uuid4().hex[:12],
        date=date,
        subject=subject,
        category=category,
        item=item,
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
        **kwargs,
    )


# ── Store layer ──────────────────────────────────────────────────────


class TestHealthStoreCRUD:
    def test_add_and_get(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        rec = _make_health()
        store.add_health_record(rec)
        got = store.get_health_record(rec.id)
        assert got is not None
        assert got.item == "头疼"
        assert got.subject == "self"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.get_health_record("nonexistent") is None

    def test_delete(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        rec = _make_health()
        store.add_health_record(rec)
        assert store.delete_health_record(rec.id) is True
        assert store.get_health_record(rec.id) is None

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.delete_health_record("nonexistent") is False

    def test_update(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        rec = _make_health(severity="mild")
        store.add_health_record(rec)
        assert store.update_health_record(rec.id, severity="severe") is True
        got = store.get_health_record(rec.id)
        assert got is not None
        assert got.severity == "severe"

    def test_update_ignores_id(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        rec = _make_health()
        store.add_health_record(rec)
        assert store.update_health_record(rec.id, id="hacked") is False

    def test_list_empty(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        assert store.list_health_records() == []

    def test_list_subject_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(subject="self", item="跑步"))
        store.add_health_record(_make_health(subject="小明", item="鼻炎"))
        store.add_health_record(_make_health(subject="小红", item="体检"))

        self_records = store.list_health_records(subject="self")
        assert len(self_records) == 1
        assert self_records[0].item == "跑步"

        tt_records = store.list_health_records(subject="小明")
        assert len(tt_records) == 1
        assert tt_records[0].item == "鼻炎"

    def test_list_category_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(category="fitness", item="跑步"))
        store.add_health_record(_make_health(category="symptom", item="头疼"))
        fitness = store.list_health_records(category="fitness")
        assert len(fitness) == 1
        assert fitness[0].item == "跑步"

    def test_list_date_range(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(date="2026-06-01", item="A"))
        store.add_health_record(_make_health(date="2026-06-15", item="B"))
        store.add_health_record(_make_health(date="2026-06-20", item="C"))
        result = store.list_health_records(date_from="2026-06-10", date_to="2026-06-18")
        assert len(result) == 1
        assert result[0].item == "B"

    def test_list_limit(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        for i in range(5):
            store.add_health_record(_make_health(item=f"item{i}", date=f"2026-06-{20 - i:02d}"))
        result = store.list_health_records(limit=3)
        assert len(result) == 3

    def test_categories(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(category="fitness", item="跑步"))
        store.add_health_record(_make_health(category="fitness", item="游泳"))
        store.add_health_record(_make_health(category="symptom", item="头疼"))
        cats = store.health_record_categories()
        assert cats == {"fitness": 2, "symptom": 1}

    def test_subjects(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(subject="self"))
        store.add_health_record(_make_health(subject="self"))
        store.add_health_record(_make_health(subject="小明"))
        subjects = store.health_record_subjects()
        assert subjects == {"self": 2, "小明": 1}

    def test_combined_filters(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        store.add_health_record(_make_health(subject="self", category="fitness", item="跑步", date="2026-06-15"))
        store.add_health_record(_make_health(subject="self", category="symptom", item="头疼", date="2026-06-15"))
        store.add_health_record(_make_health(subject="小明", category="fitness", item="游泳", date="2026-06-15"))
        result = store.list_health_records(subject="self", category="fitness")
        assert len(result) == 1
        assert result[0].item == "跑步"

    def test_schema_migration_idempotent(self, tmp_path: Path) -> None:
        """Re-creating the store should not fail (idempotent migration)."""
        store1 = SoloStore(tmp_path / ".solo")
        store1.add_health_record(_make_health())
        store2 = SoloStore(tmp_path / ".solo")
        assert len(store2.list_health_records()) == 1


# ── Tool handler layer ────────────────────────────────────────────────


class TestHealthRecordTool:
    @pytest.mark.asyncio
    async def test_standard_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_health_record", {
            "category": "fitness", "item": "跑步",
            "exercise_type": "跑步", "exercise_duration_min": 30,
        })
        assert "健康记录已入库" in result
        records = store.list_health_records(category="fitness")
        assert len(records) == 1
        assert records[0].exercise_duration_min == 30

    @pytest.mark.asyncio
    async def test_custom_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_health_record", {
            "category": "dental", "item": "洗牙",
        })
        assert "健康记录已入库" in result

    @pytest.mark.asyncio
    async def test_reject_vague_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_health_record", {
            "category": "other", "item": "test",
        })
        assert "too vague" in result

    @pytest.mark.asyncio
    async def test_reject_invalid_category(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry.execute("solo_health_record", {
            "category": "CamelCase", "item": "test",
        })
        assert "Invalid category" in result

    @pytest.mark.asyncio
    async def test_subject_family_member(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_health_record", {
            "category": "medical", "item": "发育评估",
            "subject": "小明",
        })
        records = store.list_health_records(subject="小明")
        assert len(records) == 1
        assert records[0].subject == "小明"

    @pytest.mark.asyncio
    async def test_pending_health_ids_tracked(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_health_record", {
            "category": "fitness", "item": "跑步",
        })
        assert len(registry._pending_health_ids) == 1

    @pytest.mark.asyncio
    async def test_metrics_json_stored(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_health_record", {
            "category": "vital", "item": "体重",
            "metrics_json": '{"weight_kg": 72.5}',
        })
        records = store.list_health_records(category="vital")
        assert records[0].metrics == {"weight_kg": 72.5}

    @pytest.mark.asyncio
    async def test_invalid_metrics_json_safe(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_health_record", {
            "category": "vital", "item": "体重",
            "metrics_json": "not-json",
        })
        records = store.list_health_records(category="vital")
        assert records[0].metrics == {}


class TestPostTurnBackfill:
    @pytest.mark.asyncio
    async def test_backfill_links_health_to_record(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)

        # Simulate solo_record creating a record
        from solo.core.models import SoloRecord
        record = SoloRecord(
            id=uuid4().hex[:12], entry_id=uuid4().hex[:12],
            date="2026-06-20", raw_content="今天跑步",
            corrected_content="今天跑步", summary="跑步",
            tags="运动", emotion="积极",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.add_record(record)
        registry._created_record_ids.add(record.id)

        # Simulate solo_health_record creating a health record
        await registry.execute("solo_health_record", {
            "category": "fitness", "item": "跑步",
        })

        # Before backfill: record_id should be empty
        health = store.list_health_records()[0]
        assert health.record_id == ""

        # After backfill: record_id should be linked
        registry.post_turn_backfill()
        health = store.list_health_records()[0]
        assert health.record_id == record.id

    @pytest.mark.asyncio
    async def test_backfill_multiple_health_records(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)

        from solo.core.models import SoloRecord
        record = SoloRecord(
            id=uuid4().hex[:12], entry_id=uuid4().hex[:12],
            date="2026-06-20", raw_content="跑步+吃药",
            corrected_content="跑步+吃药", summary="运动+用药",
            tags="运动,用药", emotion="积极",
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        store.add_record(record)
        registry._created_record_ids.add(record.id)

        await registry.execute("solo_health_record", {"category": "fitness", "item": "跑步"})
        await registry.execute("solo_health_record", {"category": "medication", "item": "布洛芬"})

        registry.post_turn_backfill()

        records = store.list_health_records()
        assert len(records) == 2
        assert all(r.record_id == record.id for r in records)

    @pytest.mark.asyncio
    async def test_backfill_no_health_records(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        # Should not raise
        registry.post_turn_backfill()

    @pytest.mark.asyncio
    async def test_backfill_no_record_created(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        await registry.execute("solo_health_record", {"category": "fitness", "item": "跑步"})
        # No record was created, so backfill should clear pending without error
        registry.post_turn_backfill()
        health = store.list_health_records()[0]
        assert health.record_id == ""  # Still empty since no record to link to


class TestHealthSummaryTool:
    @pytest.mark.asyncio
    async def test_empty_result(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        result = await registry._handle_health_summary({})
        assert result["total"] == 0

    @pytest.mark.asyncio
    async def test_with_records(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        store.add_health_record(_make_health(subject="self", category="fitness", item="跑步"))
        store.add_health_record(_make_health(subject="self", category="fitness", item="游泳"))
        store.add_health_record(_make_health(subject="小明", category="symptom", item="鼻炎"))

        result = await registry._handle_health_summary({"days": 365})
        assert result["total"] == 3
        assert result["by_category"]["fitness"] == 2
        assert result["by_subject"]["self"] == 2

    @pytest.mark.asyncio
    async def test_subject_filter(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        store.add_health_record(_make_health(subject="self", item="跑步"))
        store.add_health_record(_make_health(subject="小明", item="鼻炎"))

        result = await registry._handle_health_summary({
            "subject": "小明", "days": 365,
        })
        assert result["total"] == 1
        assert result["subject_filter"] == "小明"


class TestToolRegistration:
    def test_health_tools_registered(self, tmp_path: Path) -> None:
        store = SoloStore(tmp_path / ".solo")
        registry = SoloToolRegistry(store)
        names = {schema["name"] for schema in registry.tool_schemas()}
        assert "solo_health_record" in names
        assert "solo_health_summary" in names
