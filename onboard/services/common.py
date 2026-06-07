"""Shared helpers for onboard service implementations."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
import json
from typing import Any, AsyncIterator, Awaitable, Callable, Iterable, TypeVar


T = TypeVar("T")


async def stream_feed_digest_run(
    run_callable: Callable[..., Awaitable[Any]],
    *,
    workspace: str | None,
    preset: str | None,
) -> AsyncIterator[dict[str, Any]]:
    """Run a feed digest while yielding live progress events.

    ``run_callable`` is the app-specific ``run_feed_digest`` coroutine, which
    accepts a ``progress_callback``. Progress strings emitted by the engine are
    forwarded as ``{"type": "progress", "message": ...}`` events, followed by a
    terminal ``{"type": "done", "report": ...}`` or ``{"type": "error", ...}``.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    done = object()

    async def progress_callback(message: str) -> None:
        await queue.put({"type": "progress", "message": message})

    async def runner() -> None:
        try:
            report = await run_callable(
                workspace=workspace,
                preset_name=preset,
                progress_callback=progress_callback,
            )
            await queue.put({"type": "done", "report": to_jsonable(report)})
        except Exception as exc:  # noqa: BLE001 - surfaced to the client
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(done)  # type: ignore[arg-type]

    task = asyncio.create_task(runner())
    try:
        while True:
            event = await queue.get()
            if event is done:
                break
            yield event
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


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


def newest_first(items: Iterable[T]) -> list[T]:
    return list(reversed(list(items)))


def split_csv(value: str | None) -> list[str] | None:
    if not value:
        return None
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or None


def this_week_start(today: date | None = None) -> date:
    current = today or datetime.now().astimezone().date()
    return current - timedelta(days=current.weekday())


def current_month_range(today: date | None = None) -> tuple[date, date]:
    current = today or datetime.now().astimezone().date()
    month_start = current.replace(day=1)
    # End of month: first day of next month minus one day
    if current.month == 12:
        month_end = current.replace(year=current.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        month_end = current.replace(month=current.month + 1, day=1) - timedelta(days=1)
    return month_start, month_end


def latest_llm_usage_date(points: Iterable[dict[str, Any]], *, fallback: str) -> str:
    latest = ""
    for point in points:
        day = str(point.get("date") or "")
        if day and day > latest:
            latest = day
    return latest or fallback


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


def resolve_current_model(provider_profile: str) -> str:
    """Resolve the concrete model name for a provider profile."""
    from openharness.config.settings import load_settings

    settings = load_settings().merge_cli_overrides(active_profile=provider_profile)
    return settings.model


def resolve_vision_model() -> str:
    """Resolve the configured vision model name, or empty string."""
    from openharness.config.settings import load_settings

    settings = load_settings()
    cfg = settings.vision
    if cfg.model and cfg.api_key:
        return cfg.model
    from openharness.config.settings import VisionModelConfig

    env = VisionModelConfig.from_env()
    return env.model if env.model and env.api_key else ""
