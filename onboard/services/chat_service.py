"""WebSocket chat streaming for solo/wolo runners."""

from __future__ import annotations

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

    async for kind, text in runner.stream_run(content, session_key=session_key):
        if kind == "tool_hint":
            yield {"type": "tool_start", "tool": text, "args": {}}
        elif kind == "reasoning":
            yield {"type": "reasoning", "content": text}
        elif kind == "final":
            yield {"type": "complete", "content": text}
        elif kind == "progress":
            pass  # transient status hints; not forwarded to WebSocket UI
        else:
            yield {"type": "delta", "content": text}
