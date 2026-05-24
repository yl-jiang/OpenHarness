"""Helpers for persisting work artifacts derived from wolo records."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from wolo.core.models import WoloDecision, WoloExperiment, WoloHighlight, WoloRecord, WoloTodo
from wolo.core.store import WoloStore
from wolo.core.utils import _now


def persist_work_artifacts(
    store: WoloStore,
    record: WoloRecord,
    result: dict[str, object],
) -> None:
    for item in _dict_items(result.get("todos")):
        title = str(item.get("title") or item.get("content") or "").strip()
        if not title:
            continue
        store.add_todo(
            WoloTodo(
                id=uuid4().hex[:12],
                record_id=record.id,
                title=title,
                project=str(item.get("project") or ""),
                priority=str(item.get("priority") or "medium"),
                due_date=str(item.get("due_date") or ""),
                status=str(item.get("status") or "pending"),
                source=str(item.get("source") or "derived"),
                created_at=_now(),
            )
        )

    for item in _dict_items(result.get("decisions")):
        title = str(item.get("title") or item.get("decision") or "").strip()
        if not title:
            continue
        store.add_decision(
            WoloDecision(
                id=uuid4().hex[:12],
                record_id=record.id,
                title=title,
                rationale=str(item.get("rationale") or item.get("reason") or ""),
                impact=str(item.get("impact") or ""),
                project=str(item.get("project") or ""),
                source=str(item.get("source") or "derived"),
                created_at=_now(),
            )
        )

    for item in _dict_items(result.get("highlights")):
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or item.get("summary") or "").strip()
        if not title and not content:
            continue
        store.add_highlight(
            WoloHighlight(
                id=uuid4().hex[:12],
                record_id=record.id,
                kind=str(item.get("kind") or "important"),
                title=title or content[:80],
                content=content,
                project=str(item.get("project") or ""),
                tags=str(item.get("tags") or ""),
                source=str(item.get("source") or "derived"),
                created_at=_now(),
            )
        )

    for item in _dict_items(result.get("experiments")):
        title = str(item.get("title") or item.get("hypothesis") or "").strip()
        if not title:
            continue
        store.add_experiment(
            WoloExperiment(
                id=uuid4().hex[:12],
                record_id=record.id,
                title=title,
                hypothesis=str(item.get("hypothesis") or ""),
                problem=str(item.get("problem") or ""),
                strategy=str(item.get("strategy") or ""),
                next_move=str(item.get("next_move") or ""),
                success_signal=str(item.get("success_signal") or ""),
                deadline=str(item.get("deadline") or ""),
                project=str(item.get("project") or ""),
                status=str(item.get("status") or "active"),
                source=str(item.get("source") or "derived"),
                created_at=_now(),
            )
        )


def _dict_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]
