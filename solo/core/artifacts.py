"""Helpers for persisting personal artifacts derived from solo records."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from solo.core.models import SoloRecord, SoloTodo
from solo.core.store import SoloStore
from solo.core.utils import _now


def persist_personal_artifacts(
    store: SoloStore,
    record: SoloRecord,
    result: dict[str, object],
) -> None:
    for item in _dict_items(result.get("todos")):
        title = str(item.get("title") or item.get("content") or "").strip()
        if not title:
            continue
        store.add_todo(
            SoloTodo(
                id=uuid4().hex[:12],
                record_id=record.id,
                title=title,
                category=str(item.get("category") or ""),
                priority=str(item.get("priority") or "medium"),
                due_date=str(item.get("due_date") or ""),
                status=str(item.get("status") or "pending"),
                source=str(item.get("source") or "derived"),
                created_at=_now(),
            )
        )


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
