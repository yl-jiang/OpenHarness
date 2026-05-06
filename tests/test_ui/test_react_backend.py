"""Tests for the React backend host protocol."""

from __future__ import annotations

import asyncio
import io
import json

import pytest

from openharness.api.client import ApiMessageCompleteEvent
from openharness.api.usage import UsageSnapshot
from openharness.engine.stream_events import CompactProgressEvent, CompactProgressPhase
from openharness.engine.messages import ConversationMessage, TextBlock
from openharness.services.session_storage import load_session_snapshot
from openharness.state import AppState
from openharness.ui.backend_host import BackendHostConfig, ReactBackendHost, run_backend_host
from openharness.ui.protocol import BackendEvent
from openharness.ui.runtime import build_runtime, close_runtime, start_runtime


class StaticApiClient:
    """Fake streaming client for backend host tests."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=2, output_tokens=3),
            stop_reason=None,
        )


class FailingApiClient:
    """Fake client that triggers the query-loop ErrorEvent path."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def stream_message(self, request):
        del request
        if False:
            yield None
        raise RuntimeError(self._message)


class FakeBinaryStdout:
    """Capture protocol writes through a binary stdout buffer."""

    def __init__(self) -> None:
        self.buffer = io.BytesIO()

    def flush(self) -> None:
        return None


class _FakeBuffer:
    def __init__(self, payloads: list[bytes]) -> None:
        self._payloads = iter(payloads)

    def readline(self) -> bytes:
        return next(self._payloads, b"")


class _FakeStdin:
    def __init__(self, payloads: list[bytes]) -> None:
        self.buffer = _FakeBuffer(payloads)


def test_state_snapshot_includes_git_branch():
    event = BackendEvent.state_snapshot(
        AppState(
            model="test-model",
            permission_mode="default",
            theme="default",
            cwd="/tmp/demo",
            git_branch="main",
            keybindings={},
        )
    )

    assert event.state is not None
    assert event.state["cwd"] == "/tmp/demo"
    assert event.state["git_branch"] == "main"


@pytest.mark.asyncio
async def test_run_backend_host_accepts_permission_mode(monkeypatch):
    captured: dict[str, str | None] = {}

    async def _fake_run(self):
        captured["permission_mode"] = self._config.permission_mode
        return 0

    monkeypatch.setattr("openharness.ui.backend_host.ReactBackendHost.run", _fake_run)

    result = await run_backend_host(
        api_client=StaticApiClient("unused"),
        permission_mode="full_auto",
    )

    assert result == 0
    assert captured["permission_mode"] == "full_auto"


@pytest.mark.asyncio
async def test_read_requests_resolves_permission_response_without_queueing(monkeypatch):
    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    fut = asyncio.get_running_loop().create_future()
    host._permission_requests["req-1"] = fut

    payload = b'{"type":"permission_response","request_id":"req-1","permission_reply":"always"}\n'

    class _FakeBuffer:
        def __init__(self):
            self._reads = 0

        def readline(self):
            self._reads += 1
            if self._reads == 1:
                return payload
            return b""

    class _FakeStdin:
        buffer = _FakeBuffer()

    monkeypatch.setattr("openharness.ui.backend_host.sys.stdin", _FakeStdin())

    await host._read_requests()

    assert fut.done()
    assert fut.result() == "always"
    queued = await host._request_queue.get()
    assert queued.type == "shutdown"
    assert host._request_queue.empty()


@pytest.mark.asyncio
async def test_read_requests_maps_legacy_permission_allowed_to_once(monkeypatch):
    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    fut = asyncio.get_running_loop().create_future()
    host._permission_requests["req-1"] = fut

    payload = b'{"type":"permission_response","request_id":"req-1","allowed":true}\n'

    class _FakeBuffer:
        def __init__(self):
            self._reads = 0

        def readline(self):
            self._reads += 1
            if self._reads == 1:
                return payload
            return b""

    class _FakeStdin:
        buffer = _FakeBuffer()

    monkeypatch.setattr("openharness.ui.backend_host.sys.stdin", _FakeStdin())

    await host._read_requests()

    assert fut.done()
    assert fut.result() == "once"


@pytest.mark.asyncio
async def test_read_requests_cancels_active_line_without_queueing(monkeypatch):
    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))

    async def _pending() -> None:
        await asyncio.Future()

    task = asyncio.create_task(_pending())
    host._active_request_task = task

    payload = b'{"type":"cancel_line"}\n'

    class _FakeBuffer:
        def __init__(self):
            self._reads = 0

        def readline(self):
            self._reads += 1
            if self._reads == 1:
                return payload
            return b""

    class _FakeStdin:
        buffer = _FakeBuffer()

    monkeypatch.setattr("openharness.ui.backend_host.sys.stdin", _FakeStdin())

    await host._read_requests()

    assert task.cancelled()
    queued = await host._request_queue.get()
    assert queued.type == "shutdown"
    assert host._request_queue.empty()


@pytest.mark.asyncio
async def test_backend_host_processes_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("/version")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(event.type == "transcript_item" and event.item and event.item.role == "user" for event in events)
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "system"
        and "OpenHarness" in event.item.text
        for event in events
    )
    assert any(event.type == "state_snapshot" for event in events)


@pytest.mark.asyncio
async def test_backend_host_processes_model_turn(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("hello from react backend")))
    host._bundle = await build_runtime(api_client=StaticApiClient("hello from react backend"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(
        event.type == "assistant_complete" and event.message == "hello from react backend"
        for event in events
    )
    assert any(
        event.type == "assistant_complete"
        and event.item
        and event.item.role == "assistant"
        and "hello from react backend" in event.item.text
        for event in events
    )


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_backend_host_emits_compact_progress_event(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    async def _fake_handle_line(bundle, line, print_system, render_event, clear_output):
        del bundle, line, print_system, clear_output
        await render_event(
            CompactProgressEvent(
                phase=CompactProgressPhase.COMPACT_START,
                trigger="auto",
                message="Compacting conversation memory.",
                checkpoint="compact_start",
                metadata={"token_count": 12345},
            )
        )
        return True

    monkeypatch.setattr("openharness.ui.backend_host.handle_line", _fake_handle_line)
    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(
        event.type == "compact_progress"
        and event.compact_phase == CompactProgressPhase.COMPACT_START
        and event.compact_checkpoint == "compact_start"
        and event.compact_metadata == {"token_count": 12345}
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_run_saves_snapshot_when_exit_command_stops_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        "openharness.ui.backend_host.sys.stdin",
        _FakeStdin([b'{"type":"submit_line","line":"/exit"}\n', b""]),
    )
    monkeypatch.setattr("openharness.ui.backend_host.sys.stdout", FakeBinaryStdout())

    host = ReactBackendHost(
        BackendHostConfig(
            api_client=StaticApiClient("unused"),
            cwd=str(tmp_path),
            restore_messages=[
                ConversationMessage(role="user", content=[TextBlock(text="hello")]).model_dump(mode="json"),
                ConversationMessage(role="assistant", content=[TextBlock(text="world")]).model_dump(mode="json"),
            ],
            restore_tool_metadata={"first_user_query_at": 1735704000.0},
        )
    )

    result = await host.run()

    snapshot = load_session_snapshot(tmp_path)
    assert result == 0
    assert snapshot is not None
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][0]["content"][0]["text"] == "hello"


@pytest.mark.asyncio
async def test_backend_host_run_saves_snapshot_when_input_stream_closes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("openharness.ui.backend_host.sys.stdin", _FakeStdin([b""]))
    monkeypatch.setattr("openharness.ui.backend_host.sys.stdout", FakeBinaryStdout())

    host = ReactBackendHost(
        BackendHostConfig(
            api_client=StaticApiClient("unused"),
            cwd=str(tmp_path),
            restore_messages=[
                ConversationMessage(role="user", content=[TextBlock(text="hello")]).model_dump(mode="json"),
                ConversationMessage(role="assistant", content=[TextBlock(text="world")]).model_dump(mode="json"),
            ],
            restore_tool_metadata={"first_user_query_at": 1735704000.0},
        )
    )

    result = await host.run()

    snapshot = load_session_snapshot(tmp_path)
    assert result == 0
    assert snapshot is not None
    assert [message["role"] for message in snapshot["messages"]] == ["user", "assistant"]
    assert snapshot["messages"][1]["content"][0]["text"] == "world"


@pytest.mark.asyncio
async def test_backend_host_surfaces_query_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=FailingApiClient("rate limit")))
    host._bundle = await build_runtime(api_client=FailingApiClient("rate limit"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    assert any(event.type == "error" and "rate limit" in event.message for event in events)
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "system"
        and "rate limit" in event.item.text
        for event in events
    )


@pytest.mark.asyncio
async def test_backend_host_command_does_not_reset_cli_overrides(tmp_path, monkeypatch):
    """Regression: slash commands should not snap model/provider back to persisted defaults.

    When the session is launched with CLI overrides (e.g. --provider openai -m 5.4),
    issuing a command like /fast triggers a UI state refresh. That refresh must
    preserve the effective session settings, not reload ~/.openharness/settings.json
    verbatim.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(
        api_client=StaticApiClient("unused"),
        model="5.4",
        api_format="openai",
    )
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        # Sanity: the initial session state reflects CLI overrides.
        assert host._bundle.app_state.get().model == "5.4"
        assert host._bundle.app_state.get().provider == "openai-compatible"

        # Run a command that triggers sync_app_state.
        await host._process_line("/fast show")

        # CLI overrides should remain in effect.
        assert host._bundle.app_state.get().model == "5.4"
        assert host._bundle.app_state.get().provider == "openai-compatible"
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_backend_host_uses_effective_model_from_env_override(tmp_path, monkeypatch):
    """Regression: header model should reflect effective env override, not stale profile last_model."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_MODEL", "minimax-m1")

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        assert host._bundle.app_state.get().model == "minimax-m1"

        # Exercise sync_app_state through a slash command refresh path.
        await host._process_line("/fast show")
        assert host._bundle.app_state.get().model == "minimax-m1"
    finally:
        await close_runtime(host._bundle)


@pytest.mark.asyncio
async def test_build_runtime_leaves_interactive_sessions_unbounded_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    bundle = await build_runtime(
        api_client=StaticApiClient("unused"),
        enforce_max_turns=False,
    )
    try:
        assert bundle.engine.max_turns is None
        assert bundle.enforce_max_turns is False
    finally:
        await close_runtime(bundle)


@pytest.mark.asyncio
async def test_backend_host_emits_utf8_protocol_bytes(monkeypatch):
    host = ReactBackendHost(BackendHostConfig())
    fake_stdout = FakeBinaryStdout()
    monkeypatch.setattr("openharness.ui.backend_host.sys.stdout", fake_stdout)

    await host._emit(BackendEvent(type="assistant_delta", message="你好😊"))

    raw = fake_stdout.buffer.getvalue()
    assert raw.startswith(b"OHJSON:")
    decoded = raw.decode("utf-8").strip()
    payload = json.loads(decoded.removeprefix("OHJSON:"))
    assert payload["type"] == "assistant_delta"
    assert payload["message"] == "你好😊"


@pytest.mark.asyncio
async def test_backend_host_emits_model_select_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"), model="opus", api_format="anthropic")
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("model")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "model"
    assert any(option["value"] == "opus" and option.get("active") for option in event.select_options)
    assert any(option["value"] == "default" for option in event.select_options)


@pytest.mark.asyncio
async def test_backend_host_emits_provider_select_request_with_compact_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    monkeypatch.setattr(
        "openharness.ui.backend_host.AuthManager.get_profile_statuses",
        lambda self: {
            "deepseek": {
                "label": "DeepSeek",
                "provider": "deepseek",
                "auth_source": "deepseek_api_key",
                "configured": True,
                "active": True,
            },
            "copilot": {
                "label": "GitHub Copilot",
                "provider": "copilot",
                "auth_source": "copilot_oauth",
                "configured": False,
                "active": False,
            },
        },
    )

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("provider")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "provider"
    assert event.select_options == [
        {
            "value": "deepseek",
            "label": "DeepSeek",
            "description": "deepseek · API key",
            "active": True,
            "badge": "current",
            "badgeTone": "accent",
        },
        {
            "value": "copilot",
            "label": "GitHub Copilot",
            "description": "copilot · OAuth · Auth required",
            "active": False,
            "badge": "setup",
            "badgeTone": "warning",
        },
    ]


@pytest.mark.asyncio
async def test_backend_host_emits_theme_select_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("theme")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "theme"
    assert any(option["value"] == "default" for option in event.select_options)


@pytest.mark.asyncio
async def test_backend_host_emits_skill_select_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    visible_dir = tmp_path / "config" / "skills" / "review"
    visible_dir.mkdir(parents=True)
    (visible_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes carefully\n---\n\n# Review\nCheck the diff.",
        encoding="utf-8",
    )
    hidden_dir = tmp_path / "config" / "skills" / "internal-review"
    hidden_dir.mkdir(parents=True)
    (hidden_dir / "SKILL.md").write_text(
        "---\nname: internal-review\ndescription: Internal workflow\nuser-invocable: false\n---\n\n# Internal Review\nInternal only.",
        encoding="utf-8",
    )

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("skills")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "skills"
    assert event.modal["title"] == "Skills"
    assert any(
        option["value"] == "review"
        and option["label"] == "review"
        and "Review changes carefully" in option["description"]
        for option in event.select_options
    )
    assert all(option["value"] != "internal-review" for option in event.select_options)


@pytest.mark.asyncio
async def test_backend_host_emits_turns_select_request_with_unlimited_option(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"), enforce_max_turns=False)
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("turns")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "turns"
    assert any(option["value"] == "unlimited" and option.get("active") for option in event.select_options)


@pytest.mark.asyncio
async def test_backend_host_emits_provider_select_request(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._handle_select_command("provider")
    finally:
        await close_runtime(host._bundle)

    event = next(item for item in events if item.type == "select_request")
    assert event.modal["command"] == "provider"
    assert any(option.get("active") for option in event.select_options)


@pytest.mark.asyncio
async def test_backend_host_apply_select_command_shows_single_segment_transcript(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._apply_select_command("theme", "default")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_event = next(item for item in events if item.type == "transcript_item" and item.item and item.item.role == "user")
    assert user_event.item.text == "/theme"


@pytest.mark.asyncio
async def test_backend_host_maps_skill_selection_to_skills_command(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    skill_dir = tmp_path / "config" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review changes carefully\n---\n\n# Review\nCheck the diff.",
        encoding="utf-8",
    )

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("loaded skill")))
    host._bundle = await build_runtime(api_client=StaticApiClient("loaded skill"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._apply_select_command("skills", "review")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_event = next(item for item in events if item.type == "transcript_item" and item.item and item.item.role == "user")
    assert user_event.item.text == "/skills"
    assert any(event.type == "assistant_complete" and event.message == "loaded skill" for event in events)


@pytest.mark.asyncio
async def test_backend_host_apply_provider_select_command_shows_single_segment_transcript(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        should_continue = await host._apply_select_command("provider", "claude-api")
    finally:
        await close_runtime(host._bundle)

    assert should_continue is True
    user_event = next(item for item in events if item.type == "transcript_item" and item.item and item.item.role == "user")
    assert user_event.item.text == "/provider"


@pytest.mark.asyncio
async def test_concurrent_ask_permission_are_serialised():
    """Concurrent _ask_permission calls must be serialised so the frontend
    never receives two overlapping modal_request events.

    Without _permission_lock the second call emits a modal_request before the
    first future is resolved, overwriting the frontend's modal state. The first
    tool then silently waits 300 s and gets Permission denied.
    """
    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))

    emitted_order: list[str] = []

    async def _fake_emit(event: BackendEvent) -> None:
        if event.type == "modal_request" and event.modal:
            emitted_order.append(str(event.modal.get("request_id", "")))

    host._emit = _fake_emit  # type: ignore[method-assign]

    async def _ask_and_approve(tool: str) -> str:
        # Start the ask; a background task resolves the future once it appears.
        async def _resolver():
            # Busy-wait until this tool's future is registered.
            while True:
                await asyncio.sleep(0)
                for rid, fut in list(host._permission_requests.items()):
                    if not fut.done():
                        fut.set_result("once")
                        return

        asyncio.create_task(_resolver())
        return await host._ask_permission(tool, "reason")

    # Fire two permission requests concurrently.
    result_a, result_b = await asyncio.gather(
        _ask_and_approve("write_file"),
        _ask_and_approve("bash"),
    )

    assert result_a == "once"
    assert result_b == "once"
    # With the lock in place the two modal_request events must be emitted
    # sequentially (one completes before the other starts), so exactly two
    # distinct request IDs must have been emitted.
    assert len(emitted_order) == 2
    assert emitted_order[0] != emitted_order[1]


@pytest.mark.asyncio
async def test_ask_permission_timeout_emits_modal_clear(monkeypatch):
    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    events: list[BackendEvent] = []

    async def _fake_emit(event: BackendEvent) -> None:
        events.append(event)

    async def _fake_wait_for(awaitable, timeout):
        del awaitable
        assert timeout == 300
        raise asyncio.TimeoutError

    host._emit = _fake_emit  # type: ignore[method-assign]
    monkeypatch.setattr("openharness.ui.backend_host.asyncio.wait_for", _fake_wait_for)

    result = await host._ask_permission("write_file", "reason")

    assert result == "reject"
    assert host._permission_requests == {}
    modal_events = [event for event in events if event.type == "modal_request"]
    assert len(modal_events) == 2
    assert modal_events[0].modal == {
        "kind": "permission",
        "request_id": modal_events[0].modal["request_id"],
        "tool_name": "write_file",
        "reason": "reason",
    }
    assert modal_events[1].modal is None


@pytest.mark.asyncio
async def test_backend_host_line_complete_includes_reason_field(tmp_path, monkeypatch):
    """line_complete event must carry a 'reason' field set to 'completed'
    when the model finishes normally."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("hello reason")))
    host._bundle = await build_runtime(api_client=StaticApiClient("hello reason"))
    events = []

    async def _emit(event):
        events.append(event)

    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        await host._process_line("hi")
    finally:
        await close_runtime(host._bundle)

    line_complete_events = [e for e in events if e.type == "line_complete"]
    assert len(line_complete_events) == 1
    lc = line_complete_events[0]
    # The event must carry a reason field
    assert hasattr(lc, "reason") or (isinstance(lc, dict) and "reason" in lc)
    assert lc.reason == "completed"


@pytest.mark.asyncio
async def test_backend_host_process_line_emits_cancelled_reason_when_cancelled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENHARNESS_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))

    host = ReactBackendHost(BackendHostConfig(api_client=StaticApiClient("unused")))
    host._bundle = await build_runtime(api_client=StaticApiClient("unused"))
    events = []
    started = asyncio.Event()

    async def _emit(event):
        events.append(event)

    async def _fake_handle_line(bundle, line, print_system, render_event, clear_output):
        del bundle, line, print_system, render_event, clear_output
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("openharness.ui.backend_host.handle_line", _fake_handle_line)
    host._emit = _emit  # type: ignore[method-assign]
    await start_runtime(host._bundle)
    try:
        task = asyncio.create_task(host._process_line("hi"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        await close_runtime(host._bundle)

    line_complete_events = [event for event in events if event.type == "line_complete"]
    assert len(line_complete_events) == 1
    assert line_complete_events[0].reason == "cancelled"
    assert any(
        event.type == "transcript_item"
        and event.item
        and event.item.role == "system"
        and "cancelled" in event.item.text.lower()
        for event in events
    )
