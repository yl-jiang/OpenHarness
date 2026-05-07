"""Tests for the query engine."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from openharness.api.client import ApiMessageCompleteEvent, ApiRetryEvent, ApiTextDeltaEvent
from openharness.api.errors import RequestFailure
from openharness.api.usage import UsageSnapshot
from openharness.config.settings import PermissionSettings, Settings
from openharness.engine.messages import ConversationMessage, TextBlock, ToolUseBlock
from openharness.engine.query_engine import QueryEngine
from openharness.prompts.context import build_runtime_system_prompt
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    CompactProgressPhase,
    StatusEvent,
    StreamFinished,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.permissions import PermissionChecker, PermissionMode
from openharness.tools import create_default_tool_registry
from openharness.tools.base import BaseTool, ToolExecutionContext, ToolRegistry, ToolResult
from openharness.tools.glob_tool import GlobTool
from openharness.tools.grep_tool import GrepTool
from openharness.tools.todo_tool import TodoStore
from pydantic import BaseModel
from openharness.engine.messages import ToolResultBlock
from openharness.hooks import HookExecutionContext, HookExecutor, HookEvent
from openharness.hooks.loader import HookRegistry
from openharness.hooks.schemas import PromptHookDefinition
from openharness.engine.query import QueryContext, _execute_tool_call, _is_prompt_too_long_error
from openharness.engine.types import ToolMetadataKey


@dataclass
class _FakeResponse:
    message: ConversationMessage
    usage: UsageSnapshot


class FakeApiClient:
    """Deterministic streaming client used by query tests."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = list(responses)

    async def stream_message(self, request):
        del request
        response = self._responses.pop(0)
        for block in response.message.content:
            if isinstance(block, TextBlock) and block.text:
                yield ApiTextDeltaEvent(text=block.text)
        yield ApiMessageCompleteEvent(
            message=response.message,
            usage=response.usage,
            stop_reason=None,
        )


class StaticApiClient:
    """Fake client that always returns one fixed assistant message."""

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class RetryThenSuccessApiClient:
    async def stream_message(self, request):
        del request
        yield ApiRetryEvent(message="rate limited", attempt=1, max_attempts=4, delay_seconds=1.5)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="after retry")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class PromptTooLongThenSuccessApiClient:
    def __init__(self) -> None:
        self._calls = 0

    async def stream_message(self, request):
        self._calls += 1
        if self._calls == 1:
            raise RequestFailure("prompt too long")
        if self._calls == 2:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=[TextBlock(text="<summary>compressed</summary>")]),
                usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                stop_reason=None,
            )
            return
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="after reactive compact")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class RecordingApiClient:
    def __init__(self, text: str = "ok") -> None:
        self.requests = []
        self._text = text

    async def stream_message(self, request):
        self.requests.append(request)
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text=self._text)]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class MaxTokensTooLargeThenSuccessApiClient:
    def __init__(self) -> None:
        self.requests = []

    async def stream_message(self, request):
        self.requests.append(request)
        if len(self.requests) == 1:
            raise RequestFailure(
                "max_tokens is too large: 120000. This model supports at most "
                "32000 completion tokens, whereas you provided 120000."
            )
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="after token clamp")]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class EmptyAssistantApiClient:
    async def stream_message(self, request):
        del request
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[]),
            usage=UsageSnapshot(input_tokens=1, output_tokens=1),
            stop_reason=None,
        )


class _RecordingEvolutionController:
    def __init__(self) -> None:
        self.started: list[tuple[bool, bool]] = []
        self.observed: list[ConversationMessage] = []
        self.spawned: list[tuple[list[ConversationMessage], str]] = []

    def begin_user_turn(
        self,
        metadata: dict[str, object],
        *,
        memory_tool_available: bool,
        skill_tool_available: bool,
    ) -> None:
        del metadata
        self.started.append((memory_tool_available, skill_tool_available))

    def observe_assistant_turn(
        self,
        metadata: dict[str, object],
        message: ConversationMessage,
    ) -> None:
        del metadata
        self.observed.append(message)

    def maybe_spawn_review(
        self,
        metadata: dict[str, object],
        messages_snapshot: list[ConversationMessage],
        *,
        latest_user_prompt: str = "",
    ) -> None:
        del metadata
        self.spawned.append((messages_snapshot, latest_user_prompt))


class CoordinatorLoopApiClient:
    def __init__(self) -> None:
        self.requests = []
        self._calls = 0

    async def stream_message(self, request):
        self.requests.append(request)
        self._calls += 1
        if self._calls == 1:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(
                    role="assistant",
                    content=[
                        TextBlock(text="Launching a worker."),
                        ToolUseBlock(
                            id="toolu_agent_1",
                            name="agent",
                            input={
                                "description": "inspect coordinator wiring",
                                "prompt": "check whether coordinator mode is active",
                                "subagent_type": "worker",
                                "mode": "in_process_teammate",
                            },
                        ),
                    ],
                ),
                usage=UsageSnapshot(input_tokens=2, output_tokens=2),
                stop_reason=None,
            )
            return
        yield ApiMessageCompleteEvent(
            message=ConversationMessage(role="assistant", content=[TextBlock(text="Worker launched; coordinator mode is active.")]),
            usage=UsageSnapshot(input_tokens=2, output_tokens=2),
            stop_reason=None,
        )


class _NoopApiClient:
    async def stream_message(self, request):
        del request
        if False:
            yield None


def test_query_prompt_too_long_detection_handles_llama_cpp_errors():
    assert _is_prompt_too_long_error(
        RequestFailure("exceed_context_size_error: prompt exceeds the available context size")
    )


def test_query_prompt_too_long_detection_handles_openai_context_length_errors():
    assert _is_prompt_too_long_error(
        RequestFailure(
            "Input tokens exceed the configured limit of 922000 tokens. "
            "Your messages resulted in 3591869 tokens. Please reduce the length of the messages. "
            "code='context_length_exceeded'"
        )
    )


@pytest.mark.asyncio
async def test_query_engine_plain_text_reply(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Hello from the model.")],
                    ),
                    usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                )
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("hello")]

    assert isinstance(events[0], AssistantTextDelta)
    assert events[0].text == "Hello from the model."
    assert isinstance(events[-1], AssistantTurnComplete)
    assert engine.total_usage.input_tokens == 10
    assert engine.total_usage.output_tokens == 5
    assert len(engine.messages) == 2


@pytest.mark.asyncio
async def test_query_engine_clamps_oversized_max_tokens_before_request(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    client = RecordingApiClient()
    engine = QueryEngine(
        api_client=client,
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="openai-compatible-model",
        system_prompt="system",
        max_tokens=400_000,
    )

    events = [event async for event in engine.submit_message("hello")]

    assert client.requests[0].max_tokens == 128_000
    assert any(isinstance(event, StatusEvent) and "safe per-request output cap" in event.message for event in events)
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_query_engine_retries_with_provider_completion_token_limit(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    client = MaxTokensTooLargeThenSuccessApiClient()
    engine = QueryEngine(
        api_client=client,
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="openai-compatible-model",
        system_prompt="system",
        max_tokens=120_000,
        max_turns=1,
    )

    events = [event async for event in engine.submit_message("hello")]

    assert [request.max_tokens for request in client.requests] == [120_000, 32_000]
    assert any(isinstance(event, StatusEvent) and "provider limit 32000" in event.message for event in events)
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_query_engine_executes_tool_calls(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_123",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read the file")]

    assert any(isinstance(event, ToolExecutionStarted) for event in events)
    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert len(tool_results) == 1
    assert "alpha" in tool_results[0].output
    assert isinstance(events[-1], AssistantTurnComplete)
    assert "alpha and beta" in events[-1].message.text
    assert len(engine.messages) == 4


@pytest.mark.asyncio
async def test_query_engine_notifies_self_evolution_controller(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\n", encoding="utf-8")
    evolution = _RecordingEvolutionController()

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_123",
                                name="read_file",
                                input={"path": str(sample)},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Read it.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        tool_metadata={ToolMetadataKey.SELF_EVOLUTION_CONTROLLER.value: evolution},
    )

    events = [event async for event in engine.submit_message("read the file")]

    assert isinstance(events[-1], AssistantTurnComplete)
    assert evolution.started == [(True, True)]
    assert [message.tool_uses[0].name for message in evolution.observed if message.tool_uses] == [
        "read_file"
    ]
    assert len(evolution.spawned) == 1
    snapshot, latest_user_prompt = evolution.spawned[0]
    assert latest_user_prompt == "read the file"
    assert snapshot[-1].text == "Read it."


@pytest.mark.asyncio
async def test_query_engine_ignores_empty_invalid_parallel_tool_calls(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_valid_read",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                            ToolUseBlock(
                                id="toolu_empty_read",
                                name="read_file",
                                input={},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read the file")]

    started = [event for event in events if isinstance(event, ToolExecutionStarted)]
    completed = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert [event.tool_name for event in started] == ["read_file"]
    assert len(completed) == 1
    assert completed[0].is_error is False
    assert "alpha" in completed[0].output
    assert isinstance(events[-1], AssistantTurnComplete)
    assert "alpha and beta" in events[-1].message.text


@pytest.mark.asyncio
async def test_query_engine_reports_single_empty_invalid_tool_call(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect a file."),
                            ToolUseBlock(
                                id="toolu_empty_read",
                                name="read_file",
                                input={},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="I need a path before reading the file.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read the file")]

    completed = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert len(completed) == 1
    assert completed[0].is_error is True
    assert "Invalid input for read_file" in completed[0].output
    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "I need a path before reading the file."


@pytest.mark.asyncio
async def test_query_engine_injects_and_hides_internal_tool_name_repair_prompt(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    class RepairAwareApiClient:
        def __init__(self) -> None:
            self.requests: list[list[ConversationMessage]] = []
            self.calls = 0

        async def stream_message(self, request):
            self.requests.append(list(request.messages))
            self.calls += 1
            if self.calls == 1:
                yield ApiMessageCompleteEvent(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_repaired_read",
                                name="read",
                                input={"path": str(sample), "offset": 0, "limit": 1},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason=None,
                )
                return
            if self.calls == 2:
                last = request.messages[-1]
                assert last.role == "user"
                assert last.text.startswith("<openharness-internal:tool-name-repair>")
                assert "read -> read_file (alias)" in last.text
                assert "Do not mention this repair notice" in last.text
                yield ApiMessageCompleteEvent(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="done")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                    stop_reason=None,
                )
                return
            raise AssertionError(f"Unexpected API call count: {self.calls}")

    client = RepairAwareApiClient()
    engine = QueryEngine(
        api_client=client,
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read file")]

    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "done"
    assert client.calls == 2
    public_text = "\n".join(message.text for message in engine.messages)
    assert "<openharness-internal:tool-name-repair>" not in public_text
    assert "read -> read_file (alias)" not in public_text
    export_text = "\n".join(message.text for message in engine.export_messages)
    assert "<openharness-internal:tool-name-repair>" not in export_text
    assert "read -> read_file (alias)" not in export_text


@pytest.mark.asyncio
async def test_query_engine_auto_continues_after_empty_stop_following_tool_results(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_empty_stop",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[]),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=6, output_tokens=4),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("inspect the file thoroughly")]

    assistant_turns = [event for event in events if isinstance(event, AssistantTurnComplete)]
    status_events = [event for event in events if isinstance(event, StatusEvent)]

    assert assistant_turns[-1].message.text == "The file contains alpha and beta."
    assert not any(event.message.text == "" for event in assistant_turns)
    assert any("continuing automatically" in (event.message or "").lower() for event in status_events)
    assert engine.messages[-1].text == "The file contains alpha and beta."
    assert len(engine.messages) == 4


@pytest.mark.asyncio
async def test_query_engine_continues_again_after_two_silent_stops_with_progress_between(
    tmp_path: Path,
    monkeypatch,
):
    """When meaningful progress occurs between two consecutive silent stops, the engine
    must reset its continuation counter and fire auto-continue a second time.

    This reproduces the production scenario captured in the trace log where:
      round 1: tool work → silent stop → auto-continue #1 (succeeds)
      round 2: more tool work → silent stop again → (BUG: was capped at 1, never continued)
      round 3: model delivers final answer
    """
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "data.txt"
    sample.write_text("x\ny\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                # Round 1: tool work
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_r1",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                # Silent stop #1: empty end_turn after tool result
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[]),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=1),
                ),
                # Round 2 (after auto-continue #1): MORE tool work — meaningful progress
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_r2",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                # Silent stop #2: empty end_turn AGAIN after second tool result
                # Engine must reset attempt counter because meaningful progress occurred
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[]),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=1),
                ),
                # Round 3 (after auto-continue #2): final visible answer
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Done: data has x and y.")],
                    ),
                    usage=UsageSnapshot(input_tokens=6, output_tokens=4),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("analyse the data thoroughly")]

    assistant_turns = [e for e in events if isinstance(e, AssistantTurnComplete)]
    status_events = [e for e in events if isinstance(e, StatusEvent)]

    # The engine must reach the final turn with visible text
    assert assistant_turns[-1].message.text == "Done: data has x and y."
    # Two auto-continue status messages must have been emitted
    continuation_statuses = [e for e in status_events if "continuing automatically" in (e.message or "").lower()]
    assert len(continuation_statuses) == 2, (
        f"Expected 2 auto-continue status events, got {len(continuation_statuses)}: "
        f"{[e.message for e in status_events]}"
    )
    # Final public history ends with the visible answer
    assert engine.messages[-1].text == "Done: data has x and y."


@pytest.mark.asyncio
@pytest.mark.asyncio
async def test_query_engine_coordinator_mode_uses_coordinator_prompt_and_runs_agent_loop(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CLAUDE_CODE_COORDINATOR_MODE", "1")

    api_client = CoordinatorLoopApiClient()
    system_prompt = build_runtime_system_prompt(Settings(), cwd=tmp_path, latest_user_prompt="investigate issue")
    engine = QueryEngine(
        api_client=api_client,
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt=system_prompt,
    )

    events = [event async for event in engine.submit_message("investigate issue")]

    assert len(api_client.requests) == 2
    assert "You are a **coordinator**." in api_client.requests[0].system_prompt
    assert "Coordinator User Context" not in api_client.requests[0].system_prompt
    coordinator_context_messages = [
        msg for msg in api_client.requests[0].messages if msg.role == "user" and "Coordinator User Context" in msg.text
    ]
    assert len(coordinator_context_messages) == 1
    assert "Workers spawned via the agent tool have access to these tools" in coordinator_context_messages[0].text
    assert any(isinstance(event, ToolExecutionStarted) and event.tool_name == "agent" for event in events)
    agent_results = [event for event in events if isinstance(event, ToolExecutionCompleted) and event.tool_name == "agent"]
    assert len(agent_results) == 1
    assert isinstance(events[-1], AssistantTurnComplete)
    assert "coordinator mode is active" in events[-1].message.text


@pytest.mark.asyncio
async def test_query_engine_allows_unbounded_turns_when_max_turns_is_none(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will inspect the file."),
                            ToolUseBlock(
                                id="toolu_123",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="The file contains alpha and beta.")],
                    ),
                    usage=UsageSnapshot(input_tokens=8, output_tokens=6),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_turns=None,
    )

    events = [event async for event in engine.submit_message("read the file")]

    assert isinstance(events[-1], AssistantTurnComplete)
    assert "alpha and beta" in events[-1].message.text
    assert engine.max_turns is None


@pytest.mark.asyncio
async def test_query_engine_surfaces_retry_status_events(tmp_path: Path):
    engine = QueryEngine(
        api_client=RetryThenSuccessApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("hello")]

    assert any(isinstance(event, StatusEvent) and "retrying in 1.5s" in event.message for event in events)
    assert isinstance(events[-1], AssistantTurnComplete)


@pytest.mark.asyncio
async def test_query_engine_emits_compact_progress_before_reply(tmp_path: Path, monkeypatch):
    long_text = "alpha " * 50000
    monkeypatch.setattr("openharness.services.compact.try_session_memory_compaction", lambda *args, **kwargs: None)
    monkeypatch.setattr("openharness.services.compact.should_autocompact", lambda *args, **kwargs: True)
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="<summary>trimmed</summary>")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="after compact")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-sonnet-4-6",
        system_prompt="system",
    )
    engine.load_messages(
        [
            ConversationMessage(role="user", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="assistant", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="user", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="assistant", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="user", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="assistant", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="user", content=[TextBlock(text=long_text)]),
            ConversationMessage(role="assistant", content=[TextBlock(text=long_text)]),
        ]
    )

    events = [event async for event in engine.submit_message("hello")]

    hooks_start_index = next(
        i for i, event in enumerate(events) if isinstance(event, CompactProgressEvent) and event.phase == CompactProgressPhase.HOOKS_START
    )
    compact_start_index = next(
        i for i, event in enumerate(events) if isinstance(event, CompactProgressEvent) and event.phase == CompactProgressPhase.COMPACT_START
    )
    final_index = next(i for i, event in enumerate(events) if isinstance(event, AssistantTurnComplete))
    assert hooks_start_index < compact_start_index
    assert compact_start_index < final_index
    assert any(isinstance(event, CompactProgressEvent) and event.phase == CompactProgressPhase.COMPACT_END for event in events)


@pytest.mark.asyncio
async def test_query_engine_reactive_compacts_after_prompt_too_long(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("openharness.services.compact.try_session_memory_compaction", lambda *args, **kwargs: None)
    monkeypatch.setattr("openharness.services.compact.should_autocompact", lambda *args, **kwargs: False)
    engine = QueryEngine(
        api_client=PromptTooLongThenSuccessApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )
    engine.load_messages(
        [
            ConversationMessage(role="user", content=[TextBlock(text="one")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="two")]),
            ConversationMessage(role="user", content=[TextBlock(text="three")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="four")]),
            ConversationMessage(role="user", content=[TextBlock(text="five")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="six")]),
            ConversationMessage(role="user", content=[TextBlock(text="seven")]),
            ConversationMessage(role="assistant", content=[TextBlock(text="eight")]),
        ]
    )

    events = [event async for event in engine.submit_message("nine")]

    assert any(
        isinstance(event, CompactProgressEvent)
        and event.trigger == "reactive"
        and event.phase == CompactProgressPhase.COMPACT_START
        for event in events
    )
    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "after reactive compact"


@pytest.mark.asyncio
async def test_query_engine_tracks_recent_read_files_and_skills(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")
    registry = create_default_tool_registry()
    skill_tool = registry.get("skill_manager")
    assert skill_tool is not None

    async def _fake_skill_execute(arguments, context):
        del context
        return ToolResult(output=f"Loaded skill: {arguments.name}")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(skill_tool, "execute", _fake_skill_execute)

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(name="read_file", input={"path": str(sample)}),
                            ToolUseBlock(name="skill_manager", input={"action": "load", "name": "demo-skill"}),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        tool_metadata={},
    )

    try:
        events = [event async for event in engine.submit_message("track context")]
    finally:
        monkeypatch.undo()

    assert isinstance(events[-1], AssistantTurnComplete)
    read_state = engine._tool_metadata.get("read_file_state")
    assert isinstance(read_state, list) and read_state
    assert read_state[-1]["path"] == str(sample.resolve())
    assert "alpha" in read_state[-1]["preview"]
    task_focus = engine.tool_metadata.get("task_focus_state")
    assert isinstance(task_focus, dict)
    assert "track context" in task_focus.get("goal", "")
    assert str(sample.resolve()) in task_focus.get("active_artifacts", [])
    invoked_skills = engine._tool_metadata.get("invoked_skills")
    assert isinstance(invoked_skills, list)
    assert invoked_skills[-1] == "demo-skill"
    verified = engine.tool_metadata.get("recent_verified_work")
    assert isinstance(verified, list)
    assert any("Inspected file" in entry for entry in verified)
    assert any("Loaded skill demo-skill" in entry for entry in verified)


@pytest.mark.asyncio
async def test_query_engine_tracks_async_agent_activity(tmp_path: Path, monkeypatch):
    registry = create_default_tool_registry()
    agent_tool = registry.get("agent")
    assert agent_tool is not None

    async def _fake_execute(arguments, context):
        del arguments, context
        return ToolResult(output="Spawned agent worker@team (task_id=task_123, backend=subprocess)")

    monkeypatch.setattr(agent_tool, "execute", _fake_execute)
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                name="agent",
                                input={"description": "Inspect CI", "prompt": "Inspect CI"},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="spawned")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        tool_metadata={},
    )

    events = [event async for event in engine.submit_message("spawn helper")]

    assert isinstance(events[-1], AssistantTurnComplete)
    async_state = engine._tool_metadata.get("async_agent_state")
    assert isinstance(async_state, list)
    assert async_state[-1].startswith("Spawned async agent")


@pytest.mark.asyncio
async def test_query_engine_respects_pre_tool_hook_blocks(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\n", encoding="utf-8")
    registry = HookRegistry()
    registry.register(
        HookEvent.PRE_TOOL_USE,
        PromptHookDefinition(prompt="reject", matcher="read_file"),
    )

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_999",
                                name="read_file",
                                input={"path": str(sample)},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        hook_executor=HookExecutor(
            registry,
            HookExecutionContext(
                cwd=tmp_path,
                api_client=StaticApiClient('{"ok": false, "reason": "no reading"}'),
                default_model="claude-test",
            ),
        ),
    )

    events = [event async for event in engine.submit_message("read file")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].is_error is True
    assert "no reading" in tool_results[0].output


def _tool_context(tmp_path: Path, registry: ToolRegistry, settings: PermissionSettings) -> QueryContext:
    return QueryContext(
        api_client=_NoopApiClient(),
        tool_registry=registry,
        permission_checker=PermissionChecker(settings),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
        max_turns=1,
    )


@pytest.mark.asyncio
async def test_execute_tool_call_blocks_sensitive_directory_roots(tmp_path: Path):
    sensitive_dir = tmp_path / ".ssh"
    sensitive_dir.mkdir()
    (sensitive_dir / "id_rsa").write_text("PRIVATE KEY MATERIAL\n", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(GrepTool())

    result = await _execute_tool_call(
        _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.DEFAULT)),
        "grep",
        "toolu_grep",
        {"pattern": "PRIVATE", "root": str(sensitive_dir), "file_glob": "*"},
    )

    assert result.is_error is True
    assert "sensitive credential path" in result.content


@pytest.mark.asyncio
async def test_execute_tool_call_applies_path_rules_to_directory_roots(tmp_path: Path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    (blocked_dir / "secret.txt").write_text("classified\n", encoding="utf-8")

    registry = ToolRegistry()
    registry.register(GlobTool())

    result = await _execute_tool_call(
        _tool_context(
            tmp_path,
            registry,
            PermissionSettings(
                mode=PermissionMode.DEFAULT,
                path_rules=[{"pattern": str(blocked_dir) + "/*", "allow": False}],
            ),
        ),
        "glob",
        "toolu_glob",
        {"pattern": "*", "root": str(blocked_dir)},
    )

    assert result.is_error is True
    assert str(blocked_dir) in result.content


@pytest.mark.asyncio
async def test_execute_tool_call_returns_actionable_reason_when_user_denies_confirmation(tmp_path: Path):
    async def _deny(_tool_name: str, _reason: str) -> bool:
        return False

    result = await _execute_tool_call(
        QueryContext(
            api_client=_NoopApiClient(),
            tool_registry=create_default_tool_registry(),
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT)),
            cwd=tmp_path,
            model="claude-test",
            system_prompt="system",
            max_tokens=1,
            max_turns=1,
            permission_prompt=_deny,
        ),
        "bash",
        "toolu_bash",
        {"command": "mkdir -p scratch-dir"},
    )

    assert result.is_error is True
    assert "Mutating tools require user confirmation" in result.content
    assert "/permissions full_auto" in result.content


@pytest.mark.asyncio
async def test_execute_tool_call_remembers_always_permission_reply(tmp_path: Path):
    async def _allow_always(_tool_name: str, _reason: str) -> str:
        return "always"

    checker = PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT))
    context = QueryContext(
        api_client=_NoopApiClient(),
        tool_registry=create_default_tool_registry(),
        permission_checker=checker,
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
        max_turns=1,
        permission_prompt=_allow_always,
    )

    first = await _execute_tool_call(
        context,
        "bash",
        "toolu_bash_1",
        {"command": "mkdir -p scratch-dir"},
    )
    second = await _execute_tool_call(
        context,
        "bash",
        "toolu_bash_2",
        {"command": "mkdir -p another-dir"},
    )

    assert first.is_error is False
    assert second.is_error is False
    assert (tmp_path / "scratch-dir").is_dir()
    assert (tmp_path / "another-dir").is_dir()


@pytest.mark.asyncio
async def test_concurrent_same_tool_calls_reuse_always_permission_reply(tmp_path: Path):
    class MarkerInput(BaseModel):
        value: str

    class MarkerTool(BaseTool):
        name = "marker"
        description = "Return a marker"
        input_model = MarkerInput

        async def execute(self, arguments: MarkerInput, context: ToolExecutionContext) -> ToolResult:
            del context
            return ToolResult(output=arguments.value)

    prompt_calls: list[str] = []

    async def _allow_always(tool_name: str, _reason: str) -> str:
        prompt_calls.append(tool_name)
        if len(prompt_calls) == 1:
            await asyncio.sleep(0.05)
        return "always"

    registry = ToolRegistry()
    registry.register(MarkerTool())
    context = QueryContext(
        api_client=_NoopApiClient(),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.DEFAULT)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_tokens=1,
        max_turns=1,
        permission_prompt=_allow_always,
    )

    first, second = await asyncio.gather(
        _execute_tool_call(context, "marker", "toolu_marker_1", {"value": "first"}),
        _execute_tool_call(context, "marker", "toolu_marker_2", {"value": "second"}),
    )

    assert first.is_error is False
    assert second.is_error is False
    assert prompt_calls == ["marker"]


@pytest.mark.asyncio
async def test_execute_tool_call_returns_error_when_tool_raises(tmp_path: Path):
    class ExplodingInput(BaseModel):
        pass

    class ExplodingTool(BaseTool):
        name = "explode"
        description = "Raise an exception"
        input_model = ExplodingInput

        async def execute(self, arguments: ExplodingInput, context: ToolExecutionContext) -> ToolResult:
            del arguments, context
            raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(ExplodingTool())

    result = await _execute_tool_call(
        _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        "explode",
        "toolu_explode",
        {},
    )

    assert result.is_error is True
    assert result.content == "Tool explode failed: RuntimeError: boom"


@pytest.mark.asyncio
async def test_execute_tool_call_repairs_tool_name_before_execution(tmp_path: Path):
    class MarkerInput(BaseModel):
        value: str

    class MarkerTool(BaseTool):
        name = "marker_tool"
        description = "Return a marker"
        input_model = MarkerInput

        async def execute(self, arguments: MarkerInput, context: ToolExecutionContext) -> ToolResult:
            del context
            return ToolResult(output=f"marker:{arguments.value}")

    registry = ToolRegistry()
    registry.register(MarkerTool())

    result = await _execute_tool_call(
        _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        "MARKER_TOOL",
        "toolu_marker",
        {"value": "ok"},
    )

    assert result.is_error is False
    assert result.content == "marker:ok"


@pytest.mark.asyncio
async def test_execute_tool_call_records_tool_name_repair_notice_in_tool_metadata(tmp_path: Path):
    sample = tmp_path / "hello.txt"
    sample.write_text("alpha\nbeta\n", encoding="utf-8")
    context = _tool_context(
        tmp_path,
        create_default_tool_registry(),
        PermissionSettings(mode=PermissionMode.FULL_AUTO),
    )
    context.tool_metadata = {}

    result = await _execute_tool_call(
        context,
        "read",
        "toolu_read_alias",
        {"path": str(sample), "offset": 0, "limit": 1},
    )

    assert result.is_error is False
    assert context.tool_metadata[ToolMetadataKey.TOOL_NAME_REPAIR_NOTICES.value] == [
        {
            "requested_name": "read",
            "resolved_name": "read_file",
            "reason": "alias",
            "tool_use_id": "toolu_read_alias",
        }
    ]


@pytest.mark.asyncio
async def test_execute_tool_call_runs_hooks_with_repaired_tool_name(tmp_path: Path):
    class MarkerInput(BaseModel):
        value: str

    class MarkerTool(BaseTool):
        name = "marker_tool"
        description = "Return a marker"
        input_model = MarkerInput

        async def execute(self, arguments: MarkerInput, context: ToolExecutionContext) -> ToolResult:
            del context
            return ToolResult(output=f"marker:{arguments.value}")

    @dataclass
    class _HookResult:
        blocked: bool = False
        reason: str | None = None

    class _FakeHookExecutor:
        def __init__(self) -> None:
            self.pre_tool_names: list[str] = []

        async def execute(self, event: HookEvent, payload: dict[str, object]) -> _HookResult:
            if event == HookEvent.PRE_TOOL_USE:
                self.pre_tool_names.append(str(payload["tool_name"]))
            return _HookResult()

    registry = ToolRegistry()
    registry.register(MarkerTool())
    hook_executor = _FakeHookExecutor()
    context = _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.FULL_AUTO))
    context.hook_executor = hook_executor  # type: ignore[assignment]

    result = await _execute_tool_call(
        context,
        "MARKER_TOOL",
        "toolu_marker",
        {"value": "ok"},
    )

    assert result.is_error is False
    assert hook_executor.pre_tool_names == ["marker_tool"]


@pytest.mark.asyncio
async def test_execute_tool_call_returns_structured_invalid_tool_result(tmp_path: Path):
    registry = ToolRegistry()
    registry.register(GrepTool())
    registry.register(GlobTool())

    result = await _execute_tool_call(
        _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        "grebble",
        "toolu_bad",
        {"pattern": "x"},
    )

    assert result.is_error is True
    assert '"error_type": "invalid_tool"' in result.content
    assert '"requested_tool": "grebble"' in result.content
    assert '"available_tools": [' in result.content


@pytest.mark.asyncio
async def test_execute_tool_call_blocks_repeated_identical_failures(tmp_path: Path):
    class FailingInput(BaseModel):
        value: str

    class FailingTool(BaseTool):
        name = "always_fail"
        description = "Always fail"
        input_model = FailingInput

        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, arguments: FailingInput, context: ToolExecutionContext) -> ToolResult:
            del arguments, context
            self.calls += 1
            return ToolResult(output="same failure", is_error=True)

    tool = FailingTool()
    registry = ToolRegistry()
    registry.register(tool)
    context = _tool_context(tmp_path, registry, PermissionSettings(mode=PermissionMode.FULL_AUTO))
    context.tool_metadata = {}

    for index in range(3):
        result = await _execute_tool_call(
            context,
            "always_fail",
            f"toolu_fail_{index}",
            {"value": "x"},
        )
        assert result.content == "same failure"

    blocked = await _execute_tool_call(
        context,
        "always_fail",
        "toolu_fail_blocked",
        {"value": "x"},
    )

    assert blocked.is_error is True
    assert "3 consecutive identical failing calls" in blocked.content
    assert "Try a different approach" in blocked.content
    assert tool.calls == 3


@pytest.mark.asyncio
async def test_query_engine_executes_ask_user_tool(tmp_path: Path):
    async def _answer(question: str) -> str:
        assert question == "Which color?"
        return "green"

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_ask",
                                name="ask_user_question",
                                input={"question": "Which color?"},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Picked green.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        ask_user_prompt=_answer,
    )

    events = [event async for event in engine.submit_message("pick a color")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].output == "green"
    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "Picked green."


@pytest.mark.asyncio
async def test_query_engine_applies_path_rules_to_relative_read_file_targets(tmp_path: Path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    secret = blocked_dir / "secret.txt"
    secret.write_text("top-secret\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_blocked_read",
                                name="read_file",
                                input={"path": "blocked/secret.txt", "offset": 0, "limit": 1},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.DEFAULT,
                path_rules=[{"pattern": str((blocked_dir / "*").resolve()), "allow": False}],
            )
        ),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("read blocked file")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].is_error is True
    assert "matches deny rule" in tool_results[0].output


@pytest.mark.asyncio
async def test_query_engine_applies_path_rules_to_write_file_targets_in_full_auto(tmp_path: Path):
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    target = blocked_dir / "output.txt"

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_blocked_write",
                                name="write_file",
                                input={"path": "blocked/output.txt", "content": "poc"},
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="blocked")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(
            PermissionSettings(
                mode=PermissionMode.FULL_AUTO,
                path_rules=[{"pattern": str((blocked_dir / "*").resolve()), "allow": False}],
            )
        ),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("write blocked file")]

    tool_results = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert tool_results
    assert tool_results[0].is_error is True
    assert "matches deny rule" in tool_results[0].output
    assert target.exists() is False


class _OkInput(BaseModel):
    pass


class _OkTool(BaseTool):
    name = "ok_tool"
    description = "Returns success."
    input_model = _OkInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        return ToolResult(output="ok")


class _BoomTool(BaseTool):
    name = "boom_tool"
    description = "Always raises."
    input_model = _OkInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        raise RuntimeError("boom")


class _LargeOutputTool(BaseTool):
    name = "mcp__playwright__browser_snapshot"
    description = "Returns a large browser snapshot."
    input_model = _OkInput

    def is_read_only(self, arguments: BaseModel) -> bool:
        return True

    async def execute(self, arguments: BaseModel, context: ToolExecutionContext) -> ToolResult:
        del arguments, context
        return ToolResult(output="snapshot-line\n" * 40)


@pytest.mark.asyncio
async def test_query_engine_synthesizes_tool_result_when_parallel_tool_raises(tmp_path: Path):
    """Parallel tool calls must each yield a tool_result even when one tool raises.

    Regression for the case where ``asyncio.gather`` (without
    ``return_exceptions=True``) propagated the first exception, abandoned the
    sibling coroutines, and left the conversation with un-replied ``tool_use``
    blocks — Anthropic's API then rejects the next request on the session.
    """

    registry = ToolRegistry()
    registry.register(_OkTool())
    registry.register(_BoomTool())

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="Running two tools."),
                            ToolUseBlock(id="toolu_ok", name="ok_tool", input={}),
                            ToolUseBlock(id="toolu_boom", name="boom_tool", input={}),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Recovered from the failure.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("run both tools")]

    completed = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    completed_by_name = {event.tool_name: event for event in completed}
    assert set(completed_by_name) == {"ok_tool", "boom_tool"}
    assert completed_by_name["ok_tool"].is_error is False
    assert completed_by_name["ok_tool"].output == "ok"
    assert completed_by_name["boom_tool"].is_error is True
    assert "RuntimeError" in completed_by_name["boom_tool"].output
    assert "boom" in completed_by_name["boom_tool"].output

    user_tool_messages = [
        msg for msg in engine.messages if msg.role == "user" and any(isinstance(block, ToolResultBlock) for block in msg.content)
    ]
    assert len(user_tool_messages) == 1
    result_blocks = [block for block in user_tool_messages[0].content if isinstance(block, ToolResultBlock)]
    assert {block.tool_use_id for block in result_blocks} == {"toolu_ok", "toolu_boom"}

    assert isinstance(events[-1], AssistantTurnComplete)
    assert events[-1].message.text == "Recovered from the failure."


@pytest.mark.asyncio
async def test_query_engine_offloads_large_tool_result_outputs(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OPENHARNESS_TOOL_OUTPUT_INLINE_CHARS", "256")
    monkeypatch.setenv("OPENHARNESS_TOOL_OUTPUT_PREVIEW_CHARS", "128")
    registry = ToolRegistry()
    registry.register(_LargeOutputTool())

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_snapshot",
                                name="mcp__playwright__browser_snapshot",
                                input={},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[TextBlock(text="done")]),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        tool_metadata={},
    )

    events = [event async for event in engine.submit_message("snapshot")]

    completed = [event for event in events if isinstance(event, ToolExecutionCompleted)]
    assert len(completed) == 1
    assert completed[0].output.startswith("[Tool output truncated]")
    assert "snapshot-line" in completed[0].output

    user_tool_messages = [
        msg for msg in engine.messages if msg.role == "user" and any(isinstance(block, ToolResultBlock) for block in msg.content)
    ]
    result_blocks = [block for block in user_tool_messages[0].content if isinstance(block, ToolResultBlock)]
    inline = result_blocks[0].content
    assert "Full output saved to:" in inline
    assert "Original size:" in inline
    assert inline.count("snapshot-line") < 40
    artifact_line = next(line for line in inline.splitlines() if line.startswith("Full output saved to:"))
    artifact_path = Path(artifact_line.removeprefix("Full output saved to:").strip())
    assert artifact_path.exists()
    assert artifact_path.read_text(encoding="utf-8") == "snapshot-line\n" * 40
    assert str(artifact_path) in engine.tool_metadata["task_focus_state"]["active_artifacts"]


@pytest.mark.asyncio
async def test_query_engine_yields_stream_finished_when_auto_continue_exhausted(
    tmp_path: Path,
    monkeypatch,
):
    """When auto-continue budget is exhausted the engine must yield
    StreamFinished(reason='auto_continue_exhausted') as the final event.

    With _MAX_AUTO_CONTINUE_ABSOLUTE patched to 1:
      round 1: tool work -> silent stop -> auto-continue #1
      round 2: more tool work -> silent stop -> budget exhausted -> StreamFinished
    """
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    monkeypatch.setattr("openharness.engine.query_engine._MAX_AUTO_CONTINUE_ABSOLUTE", 1)
    sample = tmp_path / "data.txt"
    sample.write_text("x\ny\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                # Round 1: tool work
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_ex1",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                # Silent stop #1: empty end_turn after tool result
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[]),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=1),
                ),
                # Round 2 (after auto-continue #1): MORE tool work
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_ex2",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 2},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
                # Silent stop #2: budget exhausted (_MAX_AUTO_CONTINUE_ABSOLUTE=1)
                _FakeResponse(
                    message=ConversationMessage(role="assistant", content=[]),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
    )

    events = [event async for event in engine.submit_message("analyse the data")]

    stream_finished = [e for e in events if isinstance(e, StreamFinished)]
    assert len(stream_finished) == 1, (
        f"Expected exactly one StreamFinished event; got {len(stream_finished)}: "
        f"{[e for e in events]}"
    )
    assert stream_finished[0].reason == "auto_continue_exhausted"


@pytest.mark.asyncio
async def test_query_engine_max_turns_exceeded_yields_stream_finished_not_exception(
    tmp_path: Path,
    monkeypatch,
):
    """When max_turns is reached the engine must yield StreamFinished(reason='max_turns_exceeded')
    and must NOT raise MaxTurnsExceeded as an exception.

    max_turns=1 with a tool-calling model needs 2 API calls (call + follow-up);
    the engine must catch MaxTurnsExceeded internally and convert it to an event.
    """
    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    sample = tmp_path / "data.txt"
    sample.write_text("hello\n", encoding="utf-8")

    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                # Single tool-calling response; needs a follow-up but max_turns=1
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_mt1",
                                name="read_file",
                                input={"path": str(sample), "offset": 0, "limit": 1},
                            ),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=4, output_tokens=3),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings()),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        max_turns=1,
    )

    # Must NOT raise MaxTurnsExceeded
    events = [event async for event in engine.submit_message("read the file")]

    stream_finished = [e for e in events if isinstance(e, StreamFinished)]
    assert len(stream_finished) == 1, (
        f"Expected exactly one StreamFinished event; got {len(stream_finished)}: "
        f"{[e for e in events]}"
    )
    assert stream_finished[0].reason == "max_turns_exceeded"


class _RecordingHookExecutor:
    """Duck-typed hook executor that records every fired event + payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[HookEvent, dict]] = []

    async def execute(self, event: HookEvent, payload: dict):
        from openharness.hooks.types import AggregatedHookResult

        self.calls.append((event, dict(payload)))
        return AggregatedHookResult(results=[])


@pytest.mark.asyncio
async def test_subagent_stop_hook_fires_when_spawned_agent_finishes(tmp_path: Path, monkeypatch):
    import asyncio

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    monkeypatch.setenv("OPENHARNESS_DATA_DIR", str(tmp_path / "data"))
    recorder = _RecordingHookExecutor()
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            ToolUseBlock(
                                id="toolu_agent_1",
                                name="agent",
                                input={
                                    "description": "quick worker run",
                                    "prompt": "ready",
                                    "subagent_type": "worker",
                                    "mode": "local_agent",
                                    "command": 'python -u -c "import sys; print(sys.stdin.readline().strip())"',
                                },
                            )
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=2, output_tokens=2),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="worker done")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=create_default_tool_registry(),
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        hook_executor=recorder,  # type: ignore[arg-type]
    )

    _ = [event async for event in engine.submit_message("run a worker")]

    from openharness.tasks import get_task_manager

    manager = get_task_manager()
    deadline = asyncio.get_running_loop().time() + 2.0
    while asyncio.get_running_loop().time() < deadline:
        subagent_stop_calls = [c for c in recorder.calls if c[0] == HookEvent.SUBAGENT_STOP]
        if subagent_stop_calls:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("subagent_stop hook did not fire")

    subagent_stop_calls = [c for c in recorder.calls if c[0] == HookEvent.SUBAGENT_STOP]
    assert len(subagent_stop_calls) == 1
    payload = subagent_stop_calls[0][1]
    assert payload["event"] == "subagent_stop"
    assert payload["agent_id"] == "worker@default"
    assert payload["subagent_type"] == "worker"
    assert payload["mode"] == "local_agent"
    assert payload["status"] == "completed"
    assert payload["return_code"] == 0

    task = manager.get_task(payload["task_id"])
    assert task is not None
    assert task.status == "completed"


@pytest.mark.asyncio
async def test_query_engine_persists_compacted_tool_turn_history(tmp_path: Path, monkeypatch):
    """Compaction must not make a completed tool turn disappear from engine history."""

    monkeypatch.delenv("CLAUDE_CODE_COORDINATOR_MODE", raising=False)
    monkeypatch.setattr("openharness.services.compact.try_session_memory_compaction", lambda *args, **kwargs: None)
    should_calls = {"count": 0}

    def _should_compact_once(*args, **kwargs):
        del args, kwargs
        should_calls["count"] += 1
        return should_calls["count"] == 1

    monkeypatch.setattr("openharness.services.compact.should_autocompact", _should_compact_once)

    registry = ToolRegistry()
    registry.register(_OkTool())
    engine = QueryEngine(
        api_client=FakeApiClient(
            [
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="<summary>Earlier setup was completed.</summary>")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[
                            TextBlock(text="I will verify with a tool."),
                            ToolUseBlock(id="toolu_ok_after_compact", name="ok_tool", input={}),
                        ],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
                _FakeResponse(
                    message=ConversationMessage(
                        role="assistant",
                        content=[TextBlock(text="Tool finished after compact.")],
                    ),
                    usage=UsageSnapshot(input_tokens=1, output_tokens=1),
                ),
            ]
        ),
        tool_registry=registry,
        permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
        cwd=tmp_path,
        model="claude-test",
        system_prompt="system",
        tool_metadata={"todo_store": TodoStore(tmp_path)},
    )
    engine.load_messages(
        [
            ConversationMessage.from_user_text(f"historical user request {index}")
            if index % 2 == 0
            else ConversationMessage(role="assistant", content=[TextBlock(text=f"historical answer {index}")])
            for index in range(8)
        ]
    )

    events = [event async for event in engine.submit_message("new request after compact")]

    assert any(isinstance(event, CompactProgressEvent) and event.phase == "compact_end" for event in events)
    assert any("This session is being continued" in message.text for message in engine.messages)
    assert any(
        isinstance(block, ToolUseBlock) and block.id == "toolu_ok_after_compact"
        for message in engine.messages
        for block in message.content
    )
    assert any(
        isinstance(block, ToolResultBlock) and block.tool_use_id == "toolu_ok_after_compact"
        for message in engine.messages
        for block in message.content
    )
    assert engine.messages[-1].text == "Tool finished after compact."
