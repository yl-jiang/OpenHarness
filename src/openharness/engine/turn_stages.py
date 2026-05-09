"""Turn-level pipeline stages for the query loop.

Each stage is an async generator that:
1. Receives a TurnState
2. Yields (StreamEvent, UsageSnapshot | None) tuples as side-effects
3. Sets TurnState.action to signal the orchestrator what to do next

This decomposition keeps run_query as a slim ~40-line orchestrator while
each stage is independently testable and readable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiTextDeltaEvent,
)
from openharness.api.errors import is_prompt_too_long_error as _is_prompt_too_long_error
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock, ToolResultBlock
from openharness.engine.messages import normalize_messages_for_api
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    CompactProgressEvent,
    ErrorEvent,
    StatusEvent,
    StreamEvent,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.engine.types import ToolMetadataKey
from openharness.api.provider import is_model_multimodal
from openharness.tools.base import ToolExecutionContext
from openharness.hooks import HookEvent
from openharness.tools.done_tool import DONE_TOOL_NAME
from openharness.utils.log import get_logger

logger = get_logger(__name__)

# Maximum number of times we inject a "call done()" reminder when the model
# stops without tool calls in require_explicit_done mode.
MAX_DONE_REMINDER_RETRIES = 2

IMAGE_PREPROCESS_STATUS_MESSAGE = "Converting image to text description via vision model..."

# Truncation recovery: when tool calls are dropped due to max_tokens
# truncation, multiply effective_max_tokens by this factor for the retry.
_TRUNCATION_RETRY_TOKEN_MULTIPLIER = 2.0
_MAX_EFFECTIVE_TOKENS_CAP = 128_000


class TurnAction(str, Enum):
    """Signal from a stage to the orchestrator."""

    PROCEED = "proceed"      # continue to the next stage in this turn
    RETRY_TURN = "retry"     # restart this turn (don't increment counter)
    NEXT_TURN = "next_turn"  # skip remaining stages, go to next loop iteration
    STOP = "stop"            # exit the loop


@dataclass
class TurnState:
    """Mutable state shared across stages within a single query loop run."""

    # --- Immutable context (set once at init) ---
    context: Any  # QueryContext — avoid circular import
    external_messages: list[ConversationMessage]

    # --- Per-run mutable state ---
    messages: list[ConversationMessage] = field(default_factory=list)
    turn_count: int = 0
    effective_max_tokens: int = 0
    reported_token_clamp: bool = False
    reactive_compact_attempted: bool = False
    done_reminder_count: int = 0
    session_id: str | None = None

    # Compaction state (managed by compact_stage)
    compact_state: Any = None  # AutoCompactState
    last_compaction_result: tuple[list[ConversationMessage], bool] = field(default=([], False))

    # --- Per-turn mutable state (reset each iteration) ---
    final_message: ConversationMessage | None = None
    usage: UsageSnapshot = field(default_factory=UsageSnapshot)
    stop_reason: str | None = None
    truncated_tool_calls: int = 0
    tool_calls: list[Any] = field(default_factory=list)
    tool_results: list[ToolResultBlock] = field(default_factory=list)

    # --- Stage communication ---
    action: TurnAction = TurnAction.PROCEED

    def reset_turn(self) -> None:
        """Reset per-turn fields before each iteration."""
        self.final_message = None
        self.usage = UsageSnapshot()
        self.stop_reason = None
        self.truncated_tool_calls = 0
        self.tool_calls = []
        self.tool_results = []
        self.action = TurnAction.PROCEED


# ---------------------------------------------------------------------------
# Stage 1: pre_turn — token clamp warning
# ---------------------------------------------------------------------------

async def pre_turn_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Emit a one-time warning if max_tokens was clamped."""
    if state.effective_max_tokens != state.context.max_tokens and not state.reported_token_clamp:
        state.reported_token_clamp = True
        yield StatusEvent(
            message=(
                f"Requested max_tokens={state.context.max_tokens} exceeds the safe per-request "
                f"output cap; using {state.effective_max_tokens}."
            )
        ), None


# ---------------------------------------------------------------------------
# Stage 2: compact — auto-compact check before calling the model
# ---------------------------------------------------------------------------

async def compact_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run auto-compaction if needed and handle post-compact actions."""
    from openharness.services.compact import auto_compact_if_needed

    context = state.context
    progress_queue: asyncio.Queue[CompactProgressEvent] = asyncio.Queue()

    async def _progress(event: CompactProgressEvent) -> None:
        await progress_queue.put(event)

    task = asyncio.create_task(
        auto_compact_if_needed(
            state.messages,
            api_client=context.api_client,
            model=context.model,
            system_prompt=context.system_prompt,
            state=state.compact_state,
            progress_callback=_progress,
            force=False,
            trigger="auto",
            hook_executor=context.hook_executor,
            carryover_metadata=context.tool_metadata,
            context_window_tokens=context.context_window_tokens,
            auto_compact_threshold_tokens=context.auto_compact_threshold_tokens,
        )
    )
    while True:
        try:
            event = await asyncio.wait_for(progress_queue.get(), timeout=0.05)
            yield event, None
        except asyncio.TimeoutError:
            if task.done():
                break
            continue
    while not progress_queue.empty():
        yield progress_queue.get_nowait(), None

    messages, was_compacted = await task
    state.last_compaction_result = (messages, was_compacted)

    if messages is not state.external_messages:
        state.external_messages[:] = messages
        state.messages = state.external_messages
    else:
        state.messages = messages

    if was_compacted:
        todo_store = state.context.tool_metadata.get("todo_store")
        if todo_store is not None:
            todo_msg = todo_store.format_for_injection()
            if todo_msg is not None:
                state.messages.append(ConversationMessage.from_user_content(todo_msg))
        state.context.tool_metadata.pop(ToolMetadataKey.FILE_READ_CACHE.value, None)


# ---------------------------------------------------------------------------
# Helper: image preprocessing for text-only models
# ---------------------------------------------------------------------------


async def _preprocess_images_in_messages(
    messages: list[ConversationMessage],
    context,  # QueryContext
) -> AsyncIterator[StreamEvent]:
    """Convert user image blocks to text when the active model is text-only."""
    if is_model_multimodal(context.model):
        return
    if not isinstance(context.tool_metadata, dict):
        return
    vision_config = context.tool_metadata.get(ToolMetadataKey.VISION_MODEL_CONFIG.value)
    if not isinstance(vision_config, dict) or not vision_config:
        return

    pending: list[tuple[int, int, ImageBlock]] = []
    for msg_idx, msg in enumerate(messages):
        if msg.role != "user":
            continue
        for block_idx, block in enumerate(msg.content):
            if isinstance(block, ImageBlock):
                pending.append((msg_idx, block_idx, block))
    if not pending:
        return

    yield StatusEvent(message=IMAGE_PREPROCESS_STATUS_MESSAGE)

    async def _describe(msg_idx: int, block_idx: int, block: ImageBlock) -> tuple[int, int, str]:
        tool = context.tool_registry.get("image_to_text")
        if tool is None:
            return msg_idx, block_idx, "[Image: could not describe - image_to_text tool not available]"

        tool_input = {
            "image_data": block.data,
            "media_type": block.media_type,
            "prompt": (
                "Describe this image in detail, including any text, UI elements, "
                "code, diagrams, or visual information present."
            ),
        }
        try:
            parsed = tool.input_model.model_validate(tool_input)
        except ValueError:
            return msg_idx, block_idx, "[Image: could not parse image data]"

        result = await tool.execute(
            parsed,
            ToolExecutionContext(
                cwd=context.cwd,
                metadata={
                    **context.tool_metadata,
                    ToolMetadataKey.VISION_MODEL_CONFIG.value: vision_config,
                },
            ),
        )
        if result.is_error:
            return msg_idx, block_idx, f"[Image description failed: {result.output}]"
        return msg_idx, block_idx, result.output

    results = await asyncio.gather(*(_describe(msg_idx, block_idx, block) for msg_idx, block_idx, block in pending))
    for msg_idx, block_idx, description in results:
        messages[msg_idx].content[block_idx] = TextBlock(text=description)


# ---------------------------------------------------------------------------
# Stage 3: preprocess — image conversion + message normalization
# ---------------------------------------------------------------------------


async def preprocess_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Preprocess images for text-only models and normalize messages."""
    async for event in _preprocess_images_in_messages(state.messages, state.context):
        yield event, None

    state.messages[:] = normalize_messages_for_api(state.messages)


# ---------------------------------------------------------------------------
# Stage 4: api_call — stream the model response, handle errors
# ---------------------------------------------------------------------------

async def api_call_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Call the LLM API and handle retryable errors (token limit, prompt too long)."""
    from openharness.engine.errors import _is_completion_token_limit_error, _extract_completion_token_limit
    from openharness.engine.query import (
        REACTIVE_COMPACT_STATUS_MESSAGE,
    )
    from openharness.services.compact import auto_compact_if_needed

    context = state.context
    state.final_message = None
    state.usage = UsageSnapshot()
    state.stop_reason = None

    try:
        async for event in context.api_client.stream_message(
            ApiMessageRequest(
                model=context.model,
                messages=state.messages,
                system_prompt=context.system_prompt,
                max_tokens=state.effective_max_tokens,
                tools=context.tool_registry.to_api_schema(),
            )
        ):
            if isinstance(event, ApiTextDeltaEvent):
                yield AssistantTextDelta(text=event.text), None
                continue
            if isinstance(event, ApiRetryEvent):
                yield StatusEvent(
                    message=(
                        f"Request failed; retrying in {event.delay_seconds:.1f}s "
                        f"(attempt {event.attempt + 1} of {event.max_attempts}): {event.message}"
                    )
                ), None
                continue
            if isinstance(event, ApiMessageCompleteEvent):
                state.final_message = event.message
                state.usage = event.usage
                state.stop_reason = event.stop_reason
                state.truncated_tool_calls = event.truncated_tool_calls
    except Exception as exc:
        error_msg = str(exc) or repr(exc)
        logger.event(
            "query_api_exception",
            session_id=state.session_id,
            model=context.model,
            turn_count=state.turn_count,
            error=error_msg,
            exc_type=type(exc).__name__,
            exc_repr=repr(exc),
        )
        # Handle completion token limit error — retry with lower limit
        if _is_completion_token_limit_error(exc):
            supported_limit = _extract_completion_token_limit(exc)
            if supported_limit is not None and state.effective_max_tokens > supported_limit:
                previous = state.effective_max_tokens
                state.effective_max_tokens = supported_limit
                yield StatusEvent(
                    message=(
                        f"Model rejected max_tokens={previous}; "
                        f"retrying with provider limit {state.effective_max_tokens}."
                    )
                ), None
                state.turn_count = max(0, state.turn_count - 1)
                state.action = TurnAction.RETRY_TURN
                return

        # Handle prompt-too-long error — try reactive compaction
        if not state.reactive_compact_attempted and _is_prompt_too_long_error(exc):
            state.reactive_compact_attempted = True
            yield StatusEvent(message=REACTIVE_COMPACT_STATUS_MESSAGE), None

            progress_queue: asyncio.Queue[CompactProgressEvent] = asyncio.Queue()

            async def _progress(evt: CompactProgressEvent) -> None:
                await progress_queue.put(evt)

            task = asyncio.create_task(
                auto_compact_if_needed(
                    state.messages,
                    api_client=context.api_client,
                    model=context.model,
                    system_prompt=context.system_prompt,
                    state=state.compact_state,
                    progress_callback=_progress,
                    force=True,
                    trigger="reactive",
                    hook_executor=context.hook_executor,
                    carryover_metadata=context.tool_metadata,
                    context_window_tokens=context.context_window_tokens,
                    auto_compact_threshold_tokens=context.auto_compact_threshold_tokens,
                )
            )
            while True:
                try:
                    evt = await asyncio.wait_for(progress_queue.get(), timeout=0.05)
                    yield evt, None
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    continue
            while not progress_queue.empty():
                yield progress_queue.get_nowait(), None

            messages, was_compacted = await task
            state.last_compaction_result = (messages, was_compacted)
            if messages is not state.external_messages:
                state.external_messages[:] = messages
                state.messages = state.external_messages
            else:
                state.messages = messages
            state.messages[:] = normalize_messages_for_api(state.messages)
            if was_compacted:
                state.action = TurnAction.RETRY_TURN
                return

        # Unrecoverable error
        if "connect" in error_msg.lower() or "timeout" in error_msg.lower() or "network" in error_msg.lower():
            yield ErrorEvent(message=f"Network error: {error_msg}. Check your internet connection and try again."), None
        else:
            yield ErrorEvent(message=f"API error: {error_msg}"), None
        state.action = TurnAction.STOP
        return

    if state.final_message is None:
        raise RuntimeError("Model stream finished without a final message")


# ---------------------------------------------------------------------------
# Stage 5: response_routing — handle the model's response, route to tools or exit
# ---------------------------------------------------------------------------

async def response_routing_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Process the model response: emit turn-complete, handle no-tool-use case, coordinator context."""
    context = state.context
    final_message = state.final_message
    assert final_message is not None

    # Handle coordinator context message
    coordinator_context_message: ConversationMessage | None = None
    if context.system_prompt.startswith("You are a **coordinator**."):
        if state.messages and state.messages[-1].role == "user" and state.messages[-1].text.startswith("# Coordinator User Context"):
            coordinator_context_message = state.messages.pop()

    state.messages.append(final_message)
    logger.event(
        "api_message_complete",
        session_id=state.session_id,
        model=context.model,
        turn_count=state.turn_count,
        stop_reason=state.stop_reason,
        text_length=len(final_message.text),
        tool_use_count=len(final_message.tool_uses),
        message_count=len(state.messages),
    )
    yield AssistantTurnComplete(message=final_message, usage=state.usage), state.usage

    if coordinator_context_message is not None:
        state.messages.append(coordinator_context_message)

    # No tool calls — decide whether to exit or inject done-reminder
    if not final_message.tool_uses:
        # All tool calls were truncated — bump max_tokens and retry
        if state.truncated_tool_calls > 0:
            previous = state.effective_max_tokens
            state.effective_max_tokens = min(
                int(previous * _TRUNCATION_RETRY_TOKEN_MULTIPLIER),
                _MAX_EFFECTIVE_TOKENS_CAP,
            )
            logger.event(
                "truncation_recovery",
                session_id=state.session_id,
                model=context.model,
                turn_count=state.turn_count,
                truncated_count=state.truncated_tool_calls,
                previous_max_tokens=previous,
                new_max_tokens=state.effective_max_tokens,
            )
            state.messages.append(
                ConversationMessage.from_user_text(
                    "<openharness-internal:truncation-recovery>\n"
                    f"Your previous response was truncated (stop_reason=length). "
                    f"{state.truncated_tool_calls} tool call(s) were dropped because their "
                    f"arguments were incomplete. Please retry the incomplete tool call(s). "
                    f"Output token budget has been increased from {previous} to "
                    f"{state.effective_max_tokens}."
                )
            )
            yield StatusEvent(
                message=f"Response truncated — {state.truncated_tool_calls} tool call(s) dropped; "
                        f"increasing max_tokens {previous} → {state.effective_max_tokens} for retry."
            ), None
            state.action = TurnAction.NEXT_TURN
            return

        if context.require_explicit_done and state.done_reminder_count < MAX_DONE_REMINDER_RETRIES:
            state.done_reminder_count += 1
            logger.event(
                "done_reminder_injected",
                session_id=state.session_id,
                model=context.model,
                turn_count=state.turn_count,
                reminder_count=state.done_reminder_count,
            )
            state.messages.append(
                ConversationMessage.from_user_text(
                    "<openharness-internal:done-reminder>\n"
                    "You stopped without calling the done() tool. "
                    "If the task is complete, you MUST call done(message=...) to signal completion. "
                    "If you still have work to do, continue using tools."
                )
            )
            state.action = TurnAction.NEXT_TURN
            return

        logger.event(
            "query_turn_finished_without_tool_use",
            session_id=state.session_id,
            model=context.model,
            turn_count=state.turn_count,
            text_length=len(final_message.text),
            message_count=len(state.messages),
        )
        if context.hook_executor is not None:
            await context.hook_executor.execute(
                HookEvent.STOP,
                {"event": HookEvent.STOP.value, "stop_reason": "tool_uses_empty"},
            )
        state.action = TurnAction.STOP
        return

    state.tool_calls = final_message.tool_uses


# ---------------------------------------------------------------------------
# Stage 6: done_gate — enforce done() must be called alone
# ---------------------------------------------------------------------------

async def done_gate_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Reject done() if mixed with other tools; execute non-done tools normally."""
    tool_calls = state.tool_calls
    if not tool_calls:
        return

    done_indices = [i for i, tc in enumerate(tool_calls) if tc.name == DONE_TOOL_NAME]
    if not done_indices or len(tool_calls) == 1:
        return  # no conflict — proceed to tool_execution_stage

    # done() mixed with other tools: reject done, execute the rest
    context = state.context
    from openharness.engine.query import _execute_tool_call
    tool_results = []
    for i, tc in enumerate(tool_calls):
        if i in done_indices:
            tool_results.append(ToolResultBlock(
                tool_use_id=tc.id,
                content=(
                    "Error: done() must be called alone, not alongside other tools. "
                    "Finish your remaining work first, then call done() as the only tool."
                ),
                is_error=True,
            ))
        else:
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
            result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
            yield ToolExecutionCompleted(
                tool_name=tc.name,
                output=result.content,
                is_error=result.is_error,
            ), None
            tool_results.append(result)

    state.messages.append(ConversationMessage(role="user", content=tool_results))
    logger.event(
        "done_tool_rejected_mixed",
        session_id=state.session_id,
        model=context.model,
        turn_count=state.turn_count,
        total_tools=len(tool_calls),
    )
    state.action = TurnAction.NEXT_TURN


# ---------------------------------------------------------------------------
# Stage 7: tool_execution — execute tool calls (single or parallel)
# ---------------------------------------------------------------------------

async def tool_execution_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Execute tool calls and collect results."""
    tool_calls = state.tool_calls
    if not tool_calls:
        return

    context = state.context
    from openharness.engine.query import _execute_tool_call

    if len(tool_calls) == 1:
        tc = tool_calls[0]
        yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
        result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
        yield ToolExecutionCompleted(
            tool_name=tc.name,
            output=result.content,
            is_error=result.is_error,
        ), None
        state.tool_results = [result]
    else:
        for tc in tool_calls:
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

        async def _run(tc):
            return await _execute_tool_call(context, tc.name, tc.id, tc.input)

        raw_results = await asyncio.gather(
            *[_run(tc) for tc in tool_calls], return_exceptions=True
        )
        tool_results = []
        for tc, result in zip(tool_calls, raw_results):
            if isinstance(result, BaseException):
                logger.exception(
                    "tool execution raised: name=%s id=%s",
                    tc.name,
                    tc.id,
                    exc_info=result,
                )
                result = ToolResultBlock(
                    tool_use_id=tc.id,
                    content=f"Tool {tc.name} failed: {type(result).__name__}: {result}",
                    is_error=True,
                )
            tool_results.append(result)

        for tc, result in zip(tool_calls, tool_results):
            yield ToolExecutionCompleted(
                tool_name=tc.name,
                output=result.content,
                is_error=result.is_error,
            ), None

        state.tool_results = tool_results


# ---------------------------------------------------------------------------
# Stage 8: post_tool — append results, detect done completion
# ---------------------------------------------------------------------------

async def post_tool_stage(state: TurnState) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Append tool results to messages and detect done() termination."""
    from openharness.engine.query import build_internal_tool_name_repair_prompt

    context = state.context
    tool_calls = state.tool_calls
    tool_results = state.tool_results

    state.messages.append(ConversationMessage(role="user", content=tool_results))
    repair_prompt = build_internal_tool_name_repair_prompt(context.tool_metadata)
    if repair_prompt is not None:
        state.messages.append(repair_prompt)

    logger.event(
        "tool_results_appended",
        session_id=state.session_id,
        model=context.model,
        turn_count=state.turn_count,
        tool_result_count=len(tool_results),
        error_count=sum(1 for r in tool_results if r.is_error),
        message_count=len(state.messages),
    )

    # Truncation recovery: if tool calls were dropped due to max_tokens
    # truncation, bump effective_max_tokens and inject a notice so the model
    # retries the incomplete calls in the next turn.
    if state.truncated_tool_calls > 0:
        previous = state.effective_max_tokens
        state.effective_max_tokens = min(
            int(previous * _TRUNCATION_RETRY_TOKEN_MULTIPLIER),
            _MAX_EFFECTIVE_TOKENS_CAP,
        )
        logger.event(
            "truncation_recovery",
            session_id=state.session_id,
            model=context.model,
            turn_count=state.turn_count,
            truncated_count=state.truncated_tool_calls,
            previous_max_tokens=previous,
            new_max_tokens=state.effective_max_tokens,
        )
        state.messages.append(
            ConversationMessage.from_user_text(
                "<openharness-internal:truncation-recovery>\n"
                f"Your previous response was truncated (stop_reason=length). "
                f"{state.truncated_tool_calls} tool call(s) were dropped because their "
                f"arguments were incomplete. Please retry the incomplete tool call(s). "
                f"Output token budget has been increased from {previous} to "
                f"{state.effective_max_tokens}."
            )
        )
        yield StatusEvent(
            message=f"Response truncated — {state.truncated_tool_calls} tool call(s) dropped; "
                    f"increasing max_tokens {previous} → {state.effective_max_tokens} for retry."
        ), None

    # done() as sole tool call — exit the loop
    if len(tool_calls) == 1 and tool_calls[0].name == DONE_TOOL_NAME:
        logger.event(
            "query_done_tool_called",
            session_id=state.session_id,
            model=context.model,
            turn_count=state.turn_count,
            message_count=len(state.messages),
        )
        if context.hook_executor is not None:
            await context.hook_executor.execute(
                HookEvent.STOP,
                {"event": HookEvent.STOP.value, "stop_reason": "done_tool_called"},
            )
        state.action = TurnAction.STOP


# ---------------------------------------------------------------------------
# Default stage sequence
# ---------------------------------------------------------------------------

DEFAULT_TURN_STAGES = (
    pre_turn_stage,
    compact_stage,
    preprocess_stage,
    api_call_stage,
    response_routing_stage,
    done_gate_stage,
    tool_execution_stage,
    post_tool_stage,
)
