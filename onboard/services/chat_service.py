"""WebSocket chat streaming for solo/wolo runners."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any, AsyncIterator

from solo.config import load_config as load_solo_config
from solo.core.store import SoloStore
from solo.runner import SoloQueryRunner
from wolo.config import load_config as load_wolo_config
from wolo.core.store import WoloStore
from wolo.runner import WoloQueryRunner


async def stream_chat(
    app_name: str,
    content: str,
    *,
    session_key: str,
    media: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream a chat response as onboard WebSocket protocol events."""
    if app_name == "solo":
        store = SoloStore()
        config = load_solo_config(store.workspace)
        runner = SoloQueryRunner(store, profile=config.provider_profile)
    elif app_name == "wolo":
        store = WoloStore()
        config = load_wolo_config(store.workspace)
        runner = WoloQueryRunner(store, profile=config.provider_profile)
    else:
        raise ValueError(f"Unsupported app: {app_name}")

    async for event in _stream_runner_events(runner, content, session_key=session_key, media=media):
        yield event


def _runner_event_to_ws_event(kind: str, text: str) -> dict[str, Any] | None:
    if kind == "tool_hint":
        return {"type": "tool_start", "tool": text, "args": {}}
    if kind == "reasoning":
        return {"type": "reasoning", "content": text}
    if kind == "final":
        return {"type": "complete", "content": text}
    if kind == "progress":
        return {"type": "progress", "content": text}
    if kind == "media":
        try:
            paths = json.loads(text)
            if isinstance(paths, list):
                return {"type": "media", "paths": paths}
        except (json.JSONDecodeError, TypeError):
            pass
        return None
    return {"type": "delta", "content": text}


async def _stream_runner_events(
    runner: Any,
    content: str,
    *,
    session_key: str,
    media: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    done = object()
    queue: asyncio.Queue[dict[str, Any] | BaseException | object] = asyncio.Queue()

    async def emit_progress(text: str) -> None:
        if text:
            await queue.put({"type": "progress", "content": text})

    async def produce() -> None:
        try:
            async for kind, text in runner.stream_run(
                content,
                session_key=session_key,
                media=media,
                progress_callback=emit_progress,
            ):
                event = _runner_event_to_ws_event(kind, text)
                if event is not None:
                    await queue.put(event)
        except Exception as exc:
            await queue.put(exc)
        finally:
            await queue.put(done)

    producer = asyncio.create_task(produce())
    try:
        while True:
            item = await queue.get()
            if item is done:
                break
            if isinstance(item, BaseException):
                raise item
            yield item
    finally:
        if not producer.done():
            producer.cancel()
            with suppress(asyncio.CancelledError):
                await producer
