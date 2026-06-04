import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import onboard.services.chat_service as chat_service


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("app_name", "store_attr", "config_attr", "runner_attr"),
    [
        ("solo", "SoloStore", "load_solo_config", "SoloQueryRunner"),
        ("wolo", "WoloStore", "load_wolo_config", "WoloQueryRunner"),
    ],
)
async def test_stream_chat_forwards_tool_progress_before_tool_finishes(
    monkeypatch: pytest.MonkeyPatch,
    app_name: str,
    store_attr: str,
    config_attr: str,
    runner_attr: str,
) -> None:
    release_tool = asyncio.Event()

    class FakeStore:
        workspace = "/tmp/fake-workspace"

    class FakeRunner:
        def __init__(self, store: FakeStore, *, profile: str | None = None) -> None:
            assert isinstance(store, FakeStore)
            assert profile == "test-profile"

        async def stream_run(
            self,
            content: str,
            *,
            session_key: str,
            media: Any = None,
            progress_callback: Any = None,
        ):
            assert content == "生成新闻简报"
            assert session_key == "web-session"
            yield ("tool_hint", "🛠️ 生成新闻简报\n  · domain：ai_news")

            assert progress_callback is not None
            await progress_callback("🔎 正在抓取信源")
            await release_tool.wait()

            yield ("progress", "🧠 正在提炼候选")
            yield ("delta", "处理中")
            yield ("final", "完成")

    monkeypatch.setattr(chat_service, store_attr, FakeStore)
    monkeypatch.setattr(chat_service, config_attr, lambda _workspace: SimpleNamespace(provider_profile="test-profile"))
    monkeypatch.setattr(chat_service, runner_attr, FakeRunner)

    events = chat_service.stream_chat(app_name, "生成新闻简报", session_key="web-session")
    try:
        assert await anext(events) == {
            "type": "tool_start",
            "tool": "🛠️ 生成新闻简报\n  · domain：ai_news",
            "args": {},
        }

        progress_task = asyncio.create_task(anext(events))
        assert await asyncio.wait_for(progress_task, timeout=0.2) == {
            "type": "progress",
            "content": "🔎 正在抓取信源",
        }

        release_tool.set()

        assert await anext(events) == {"type": "progress", "content": "🧠 正在提炼候选"}
        assert await anext(events) == {"type": "delta", "content": "处理中"}
        assert await anext(events) == {"type": "complete", "content": "完成"}
        with pytest.raises(StopAsyncIteration):
            await anext(events)
    finally:
        release_tool.set()
        await events.aclose()
