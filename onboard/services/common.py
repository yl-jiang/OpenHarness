"""Shared helpers for onboard service implementations."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
import json
from typing import Any, Iterable, TypeVar


T = TypeVar("T")


def to_jsonable(item: Any) -> dict[str, Any]:
    """Convert solo/wolo dataclasses and Pydantic models to JSON-safe dictionaries."""
    if hasattr(item, "to_dict"):
        data = item.to_dict()
    elif hasattr(item, "to_json"):
        data = json.loads(item.to_json())
    elif hasattr(item, "model_dump"):
        data = item.model_dump()
    elif is_dataclass(item):
        data = asdict(item)
    else:
        data = dict(item)
    return _normalize(data)


def _normalize(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _normalize(value.to_dict())
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump())
    if is_dataclass(value):
        return _normalize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def paginate(items: list[T], *, limit: int, offset: int) -> dict[str, Any]:
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def this_week_start(today: date | None = None) -> date:
    current = today or datetime.now().astimezone().date()
    return current - timedelta(days=current.weekday())


def count_this_week(records: Iterable[Any]) -> int:
    start = this_week_start()
    count = 0
    for record in records:
        date_text = str(getattr(record, "date", "") or getattr(record, "created_at", "")[:10])
        try:
            if date.fromisoformat(date_text) >= start:
                count += 1
        except ValueError:
            continue
    return count


def top_tags(records: Iterable[Any], *, limit: int = 20) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        for tag in str(getattr(record, "tags", "")).split(","):
            clean = tag.strip()
            if clean:
                counts[clean] = counts.get(clean, 0) + 1
    return [
        {"tag": tag, "count": count}
        for tag, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def emotion_distribution(records: Iterable[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        emotion = str(getattr(record, "emotion", "") or "neutral")
        counts[emotion] = counts.get(emotion, 0) + 1
    return [
        {"emotion": emotion, "count": count}
        for emotion, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def daily_counts(records: Iterable[Any]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for record in records:
        day = str(getattr(record, "date", "") or getattr(record, "created_at", "")[:10])
        if day:
            counts[day] = counts.get(day, 0) + 1
    return [
        {"date": day, "count": count}
        for day, count in sorted(counts.items(), key=lambda item: item[0])
    ]


def filter_entries(entries: list[Any], *, channel: str | None = None) -> list[Any]:
    if not channel:
        return entries
    return [entry for entry in entries if getattr(entry, "channel", "") == channel]


def filter_records(
    records: list[Any],
    *,
    tag: str | None = None,
    emotion: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[Any]:
    filtered = records
    if tag:
        needle = tag.lower()
        filtered = [record for record in filtered if needle in str(record.tags).lower()]
    if emotion:
        filtered = [record for record in filtered if record.emotion == emotion]
    if date_from:
        filtered = [record for record in filtered if str(record.date) >= date_from]
    if date_to:
        filtered = [record for record in filtered if str(record.date) <= date_to]
    return filtered


def find_by_id(items: Iterable[Any], item_id: str) -> Any | None:
    for item in items:
        if getattr(item, "id", None) == item_id:
            return item
    return None
