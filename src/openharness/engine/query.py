"""Core tool-aware query loop."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from openharness.api.client import (
    ApiMessageCompleteEvent,
    ApiMessageRequest,
    ApiRetryEvent,
    ApiTextDeltaEvent,
    SupportsStreamingMessages,
)
from openharness.api.errors import is_prompt_too_long_error as _is_prompt_too_long_error
from openharness.api.provider import is_model_multimodal
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
from openharness.engine.tool_loop_guard import (
    build_doom_loop_result,
    record_tool_call_result,
    should_block_tool_call,
)
from openharness.engine.tool_pipeline import ToolExecutionPipeline, ToolPipelineStage, ToolPipelineState
from openharness.engine.tool_repair import build_invalid_tool_result, repair_tool_name
from openharness.engine.tool_result_normalizer import TextToolResultNormalizer
from openharness.engine.types import TaskFocusStateKey, ToolMetadataKey, default_task_focus_state
from openharness.hooks import HookEvent, HookExecutor
from openharness.permissions.checker import PermissionChecker
from openharness.tools.base import ToolExecutionContext
from openharness.tools.base import ToolRegistry
from openharness.tools.done_tool import DONE_TOOL_NAME
from openharness.utils.log import get_logger

AUTO_COMPACT_STATUS_MESSAGE = "Auto-compacting conversation memory to keep things fast and focused."
REACTIVE_COMPACT_STATUS_MESSAGE = "Prompt too long; compacting conversation memory and retrying."
IMAGE_PREPROCESS_STATUS_MESSAGE = "Converting image to text description via vision model..."
MAX_SAFE_COMPLETION_TOKENS = 128_000
# Maximum number of times we inject a "call done()" reminder when the model
# stops without tool calls in require_explicit_done mode.
_MAX_DONE_REMINDER_RETRIES = 2

logger = get_logger(__name__)


PermissionPrompt = Callable[[str, str], Awaitable[object]]
AskUserPrompt = Callable[[str], Awaitable[str]]

MAX_TRACKED_READ_FILES = 6
MAX_TRACKED_SKILLS = 8
MAX_TRACKED_ASYNC_AGENT_EVENTS = 8
MAX_TRACKED_ASYNC_AGENT_TASKS = 12
MAX_TRACKED_WORK_LOG = 10
MAX_TRACKED_USER_GOALS = 5
MAX_TRACKED_ACTIVE_ARTIFACTS = 8
MAX_TRACKED_VERIFIED_WORK = 10
MAX_TRACKED_TOOL_NAME_REPAIRS = 8

INTERNAL_TOOL_NAME_REPAIR_PROMPT_PREFIX = "<openharness-internal:tool-name-repair>"
INTERNAL_DONE_REMINDER_PREFIX = "<openharness-internal:done-reminder>"


def _bounded_completion_tokens(max_tokens: int, context_window_tokens: int | None = None) -> int:
    """Return a conservative per-request output token cap."""
    limit = MAX_SAFE_COMPLETION_TOKENS
    if context_window_tokens is not None and context_window_tokens > 0:
        limit = min(limit, int(context_window_tokens))
    return max(1, min(int(max_tokens), limit))


def _extract_completion_token_limit(exc: Exception) -> int | None:
    """Parse provider errors like "supports at most 128000 completion tokens"."""
    text = str(exc).lower().replace(",", "")
    patterns = (
        r"supports at most\s+(\d+)\s+completion tokens",
        r"at most\s+(\d+)\s+completion tokens",
        r"max(?:imum)?(?:_completion)?[_\s-]tokens.*?(?:<=|less than or equal to|at most)\s+(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return max(1, int(match.group(1)))
            except ValueError:
                return None
    return None


def _is_completion_token_limit_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        ("max_tokens" in text or "max_completion_tokens" in text)
        and ("too large" in text or "at most" in text or "completion tokens" in text)
    )


class MaxTurnsExceeded(RuntimeError):
    """Raised when the agent exceeds the configured max_turns for one user prompt."""

    def __init__(self, max_turns: int) -> None:
        super().__init__(f"Exceeded maximum turn limit ({max_turns})")
        self.max_turns = max_turns


@dataclass
class QueryContext:
    """Context shared across a query run."""

    api_client: SupportsStreamingMessages
    tool_registry: ToolRegistry
    permission_checker: PermissionChecker
    cwd: Path
    model: str
    system_prompt: str
    max_tokens: int
    context_window_tokens: int | None = None
    auto_compact_threshold_tokens: int | None = None
    permission_prompt: PermissionPrompt | None = None
    ask_user_prompt: AskUserPrompt | None = None
    max_turns: int | None = 200
    hook_executor: HookExecutor | None = None
    tool_metadata: dict[str, object] | None = None
    permission_prompt_lock: asyncio.Lock | None = None
    require_explicit_done: bool = False


def _append_capped_unique(bucket: list[Any], value: Any, *, limit: int) -> None:
    if value in bucket:
        bucket.remove(value)
    bucket.append(value)
    if len(bucket) > limit:
        del bucket[:-limit]


def _task_focus_state(tool_metadata: dict[str, object] | None) -> dict[str, object]:
    if tool_metadata is None:
        return {}
    value = tool_metadata.setdefault(
        ToolMetadataKey.TASK_FOCUS_STATE.value,
        default_task_focus_state(),
    )
    if isinstance(value, dict):
        for key, default in default_task_focus_state().items():
            value.setdefault(key, default)
        return value
    replacement = default_task_focus_state()
    tool_metadata[ToolMetadataKey.TASK_FOCUS_STATE.value] = replacement
    return replacement


def _summarize_focus_text(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    return normalized[:240]


def remember_user_goal(
    tool_metadata: dict[str, object] | None,
    prompt: str,
) -> None:
    state = _task_focus_state(tool_metadata)
    summary = _summarize_focus_text(prompt)
    if not summary:
        return
    recent_goals = state.setdefault(TaskFocusStateKey.RECENT_GOALS, [])
    if isinstance(recent_goals, list):
        _append_capped_unique(recent_goals, summary, limit=MAX_TRACKED_USER_GOALS)
    state[TaskFocusStateKey.GOAL] = summary


def _remember_active_artifact(
    tool_metadata: dict[str, object] | None,
    artifact: str,
) -> None:
    normalized = artifact.strip()
    if not normalized:
        return
    state = _task_focus_state(tool_metadata)
    artifacts = state.setdefault(TaskFocusStateKey.ACTIVE_ARTIFACTS, [])
    if isinstance(artifacts, list):
        _append_capped_unique(artifacts, normalized[:240], limit=MAX_TRACKED_ACTIVE_ARTIFACTS)


def _remember_verified_work(
    tool_metadata: dict[str, object] | None,
    entry: str,
) -> None:
    normalized = entry.strip()
    if not normalized:
        return
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.RECENT_VERIFIED_WORK)
    _append_capped_unique(bucket, normalized[:320], limit=MAX_TRACKED_VERIFIED_WORK)
    state = _task_focus_state(tool_metadata)
    verified_state = state.setdefault(TaskFocusStateKey.VERIFIED_STATE, [])
    if isinstance(verified_state, list):
        _append_capped_unique(verified_state, normalized[:320], limit=MAX_TRACKED_VERIFIED_WORK)


def _remember_tool_name_repair(
    tool_metadata: dict[str, object] | None,
    *,
    requested_name: str,
    resolved_name: str,
    reason: str,
    tool_use_id: str,
) -> None:
    if requested_name == resolved_name:
        return
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.TOOL_NAME_REPAIR_NOTICES)
    entry = {
        "requested_name": requested_name,
        "resolved_name": resolved_name,
        "reason": reason,
        "tool_use_id": tool_use_id,
    }
    bucket[:] = [
        existing
        for existing in bucket
        if not isinstance(existing, dict) or str(existing.get("tool_use_id") or "") != tool_use_id
    ]
    bucket.append(entry)
    if len(bucket) > MAX_TRACKED_TOOL_NAME_REPAIRS:
        del bucket[:-MAX_TRACKED_TOOL_NAME_REPAIRS]


def _tool_metadata_bucket(
    tool_metadata: dict[str, object] | None,
    key: ToolMetadataKey | str,
) -> list[Any]:
    if tool_metadata is None:
        return []
    key_str = key.value if isinstance(key, ToolMetadataKey) else key
    value = tool_metadata.setdefault(key_str, [])
    if isinstance(value, list):
        return value
    replacement: list[Any] = []
    tool_metadata[key_str] = replacement
    return replacement


def build_internal_tool_name_repair_prompt(
    tool_metadata: dict[str, object] | None,
) -> ConversationMessage | None:
    if not isinstance(tool_metadata, dict):
        return None
    bucket = tool_metadata.get(ToolMetadataKey.TOOL_NAME_REPAIR_NOTICES.value)
    if not isinstance(bucket, list) or not bucket:
        return None

    notices: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in bucket:
        if not isinstance(raw, dict):
            continue
        requested_name = str(raw.get("requested_name") or "").strip()
        resolved_name = str(raw.get("resolved_name") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if not requested_name or not resolved_name or requested_name == resolved_name:
            continue
        key = (requested_name, resolved_name, reason)
        if key in seen:
            continue
        seen.add(key)
        notices.append(key)
    bucket.clear()
    if not notices:
        return None

    mappings = "\n".join(
        f"- {requested_name} -> {resolved_name} ({reason})"
        for requested_name, resolved_name, reason in notices
    )
    return ConversationMessage.from_user_text(
        "\n".join(
            (
                INTERNAL_TOOL_NAME_REPAIR_PROMPT_PREFIX,
                "Canonical tool name mappings for the current tool loop:",
                mappings,
                "Emit only canonical tool names in future tool_use blocks.",
                (
                    "Do not mention this repair notice, prior incorrect tool names, "
                    "or the correction itself in user-facing text unless the user explicitly "
                    "asks about tool repair, logs, or debugging."
                ),
            )
        )
    )


def _remember_read_file(
    tool_metadata: dict[str, object] | None,
    *,
    path: str,
    offset: int,
    limit: int,
    output: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.READ_FILE_STATE)
    preview_lines = [line.strip() for line in output.splitlines()[:6] if line.strip()]
    entry = {
        "path": path,
        "span": f"lines {offset + 1}-{offset + limit}",
        "preview": " | ".join(preview_lines)[:320],
        "timestamp": time.time(),
    }
    if isinstance(bucket, list):
        bucket[:] = [
            existing
            for existing in bucket
            if not isinstance(existing, dict) or str(existing.get("path") or "") != path
        ]
        bucket.append(entry)
        if len(bucket) > MAX_TRACKED_READ_FILES:
            del bucket[:-MAX_TRACKED_READ_FILES]


def _remember_skill_invocation(
    tool_metadata: dict[str, object] | None,
    *,
    skill_name: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.INVOKED_SKILLS)
    normalized = skill_name.strip()
    if not normalized:
        return
    if normalized in bucket:
        bucket.remove(normalized)
    bucket.append(normalized)
    if len(bucket) > MAX_TRACKED_SKILLS:
        del bucket[:-MAX_TRACKED_SKILLS]


def _remember_async_agent_activity(
    tool_metadata: dict[str, object] | None,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    output: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.ASYNC_AGENT_STATE)
    if tool_name == "agent":
        description = str(tool_input.get("description") or tool_input.get("prompt") or "").strip()
        summary = f"Spawned async agent. {description}".strip()
        if output.strip():
            summary = f"{summary} [{output.strip()[:180]}]".strip()
    elif tool_name == "send_message":
        target = str(tool_input.get("task_id") or "").strip()
        summary = f"Sent follow-up message to async agent {target}".strip()
    else:
        summary = output.strip()[:220] or f"Async agent activity via {tool_name}"
    bucket.append(summary)
    if len(bucket) > MAX_TRACKED_ASYNC_AGENT_EVENTS:
        del bucket[:-MAX_TRACKED_ASYNC_AGENT_EVENTS]


def _parse_spawned_agent_identity(
    output: str,
    metadata: dict[str, object] | None = None,
) -> tuple[str, str] | None:
    if isinstance(metadata, dict):
        agent_id = str(metadata.get("agent_id") or "").strip()
        task_id = str(metadata.get("task_id") or "").strip()
        if agent_id and task_id:
            return agent_id, task_id
    match = re.search(r"Spawned agent (.+?) \(task_id=(\S+?)(?:[,)]|$)", output.strip())
    if match is None:
        return None
    return match.group(1).strip(), match.group(2).strip()


def _remember_async_agent_task(
    tool_metadata: dict[str, object] | None,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    output: str,
    result_metadata: dict[str, object] | None = None,
) -> None:
    if tool_name != "agent":
        return
    identity = _parse_spawned_agent_identity(output, result_metadata)
    if identity is None:
        return
    agent_id, task_id = identity
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.ASYNC_AGENT_TASKS)
    description = str(tool_input.get("description") or tool_input.get("prompt") or "").strip()
    entry = {
        "agent_id": agent_id,
        "task_id": task_id,
        "description": description[:240],
        "status": "spawned",
        "notification_sent": False,
        "spawned_at": time.time(),
    }
    bucket[:] = [
        existing
        for existing in bucket
        if not isinstance(existing, dict) or str(existing.get("task_id") or "") != task_id
    ]
    bucket.append(entry)
    if len(bucket) > MAX_TRACKED_ASYNC_AGENT_TASKS:
        del bucket[:-MAX_TRACKED_ASYNC_AGENT_TASKS]


def _remember_work_log(
    tool_metadata: dict[str, object] | None,
    *,
    entry: str,
) -> None:
    bucket = _tool_metadata_bucket(tool_metadata, ToolMetadataKey.RECENT_WORK_LOG)
    normalized = entry.strip()
    if not normalized:
        return
    bucket.append(normalized[:320])
    if len(bucket) > MAX_TRACKED_WORK_LOG:
        del bucket[:-MAX_TRACKED_WORK_LOG]


def _update_plan_mode(tool_metadata: dict[str, object] | None, mode: str) -> None:
    if tool_metadata is None:
        return
    tool_metadata[ToolMetadataKey.PERMISSION_MODE.value] = mode


def _permission_prompt_lock(context: QueryContext) -> asyncio.Lock:
    if context.permission_prompt_lock is None:
        context.permission_prompt_lock = asyncio.Lock()
    return context.permission_prompt_lock


def _record_tool_carryover(
    context: QueryContext,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_output: str,
    tool_result_metadata: dict[str, object] | None = None,
    is_error: bool,
    resolved_file_path: str | None,
) -> None:
    if is_error:
        return
    _remember_async_agent_task(
        context.tool_metadata,
        tool_name=tool_name,
        tool_input=tool_input,
        output=tool_output,
        result_metadata=tool_result_metadata,
    )
    _carryover_state(context, tool_name=tool_name, tool_input=tool_input,
                     tool_output=tool_output, resolved_file_path=resolved_file_path)
    _carryover_log(context, tool_name=tool_name, tool_input=tool_input,
                   tool_output=tool_output, resolved_file_path=resolved_file_path)


def _carryover_state(
    context: QueryContext,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_output: str,
    resolved_file_path: str | None,
) -> None:
    """Update context awareness (active artifacts, verified work, agent state)."""
    if resolved_file_path is not None:
        _remember_active_artifact(context.tool_metadata, resolved_file_path)

    if tool_name == "read_file" and resolved_file_path is not None:
        offset = int(tool_input.get("offset") or 0)
        limit = int(tool_input.get("limit") or 200)
        _remember_read_file(
            context.tool_metadata,
            path=resolved_file_path,
            offset=offset,
            limit=limit,
            output=tool_output,
        )
        _remember_verified_work(
            context.tool_metadata,
            f"Inspected file {resolved_file_path} (lines {offset + 1}-{offset + limit})",
        )
    elif tool_name == "skill_manager":
        action = str(tool_input.get("action") or "").strip()
        skill_name = str(tool_input.get("name") or "").strip()
        if action == "load" and skill_name:
            _remember_skill_invocation(context.tool_metadata, skill_name=skill_name)
            _remember_active_artifact(context.tool_metadata, f"skill:{skill_name}")
            _remember_verified_work(context.tool_metadata, f"Loaded skill {skill_name}")
        elif action in ("write", "patch") and skill_name:
            _remember_active_artifact(context.tool_metadata, f"skill:{skill_name}")
            _remember_verified_work(context.tool_metadata, f"Wrote skill {skill_name}")
    elif tool_name in {"agent", "send_message"}:
        _remember_async_agent_activity(
            context.tool_metadata,
            tool_name=tool_name,
            tool_input=tool_input,
            output=tool_output,
        )
        description = str(tool_input.get("description") or tool_input.get("prompt") or tool_name).strip()
        _remember_verified_work(
            context.tool_metadata,
            f"Confirmed async-agent activity via {tool_name}: {description[:180]}",
        )
    elif tool_name == "plan_mode":
        action = str(tool_input.get("action") or "").strip()
        if action == "enter":
            _update_plan_mode(context.tool_metadata, "plan")
        elif action == "exit":
            _update_plan_mode(context.tool_metadata, "default")
    elif tool_name == "web_fetch":
        url = str(tool_input.get("url") or "").strip()
        if url:
            _remember_active_artifact(context.tool_metadata, url)
            _remember_verified_work(context.tool_metadata, f"Fetched remote content from {url}")
    elif tool_name == "web_search":
        query = str(tool_input.get("query") or "").strip()
        if query:
            _remember_verified_work(context.tool_metadata, f"Ran web search for {query[:180]}")
    elif tool_name == "glob":
        pattern = str(tool_input.get("pattern") or "").strip()
        if pattern:
            _remember_verified_work(context.tool_metadata, f"Expanded glob pattern {pattern[:180]}")
    elif tool_name == "grep":
        pattern = str(tool_input.get("pattern") or "").strip()
        if pattern:
            _remember_verified_work(context.tool_metadata, f"Checked repository matches for grep pattern {pattern[:180]}")
    elif tool_name == "bash":
        command = str(tool_input.get("command") or "").strip()
        summary = tool_output.splitlines()[0].strip() if tool_output.strip() else "no output"
        _remember_verified_work(
            context.tool_metadata,
            f"Ran bash command {command[:160]} [{summary[:120]}]",
        )


def _carryover_log(
    context: QueryContext,
    *,
    tool_name: str,
    tool_input: dict[str, object],
    tool_output: str,
    resolved_file_path: str | None,
) -> None:
    """Append a brief entry to the recent work log."""
    if tool_name == "read_file":
        path = resolved_file_path or str(tool_input.get("path") or "")
        _remember_work_log(context.tool_metadata, entry=f"Read file {path}")
    elif tool_name == "bash":
        command = str(tool_input.get("command") or "").strip()
        summary = tool_output.splitlines()[0].strip() if tool_output.strip() else "no output"
        _remember_work_log(context.tool_metadata, entry=f"Ran bash: {command[:160]} [{summary[:120]}]")
    elif tool_name == "grep":
        pattern = str(tool_input.get("pattern") or "").strip()
        _remember_work_log(context.tool_metadata, entry=f"Searched with grep pattern={pattern[:160]}")
    elif tool_name == "skill_manager":
        action = str(tool_input.get("action") or "").strip()
        skill_name = str(tool_input.get("name") or "").strip()
        if action == "load" and skill_name:
            _remember_work_log(context.tool_metadata, entry=f"Loaded skill {skill_name}")
        elif action in ("write", "patch") and skill_name:
            verb = "updated" if "updated" in tool_output else "created"
            _remember_work_log(context.tool_metadata, entry=f"Wrote ({verb}) skill {skill_name}")
    elif tool_name in {"agent", "send_message"}:
        _remember_work_log(context.tool_metadata, entry=f"Async agent action via {tool_name}")
    elif tool_name == "plan_mode":
        action = str(tool_input.get("action") or "").strip()
        if action == "enter":
            _remember_work_log(context.tool_metadata, entry="Entered plan mode")
        elif action == "exit":
            _remember_work_log(context.tool_metadata, entry="Exited plan mode")


async def _preprocess_images_in_messages(
    messages: list[ConversationMessage],
    context: QueryContext,
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


async def run_query(
    context: QueryContext,
    messages: list[ConversationMessage],
) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
    """Run the conversation loop until the model stops requesting tools.

    Auto-compaction is checked at the start of each turn.  When the
    estimated token count exceeds the model's auto-compact threshold,
    the engine first tries a cheap microcompact (clearing old tool result
    content) and, if that is not enough, performs a full LLM-based
    summarization of older messages.
    """
    from openharness.services.compact import (
        AutoCompactState,
        auto_compact_if_needed,
    )

    external_messages = messages
    compact_state = AutoCompactState()
    reactive_compact_attempted = False
    last_compaction_result: tuple[list[ConversationMessage], bool] = (messages, False)
    effective_max_tokens = _bounded_completion_tokens(
        context.max_tokens,
        context.context_window_tokens,
    )
    reported_token_clamp = False
    session_id = None
    if isinstance(context.tool_metadata, dict):
        raw_session_id = context.tool_metadata.get("session_id")
        if isinstance(raw_session_id, str) and raw_session_id:
            session_id = raw_session_id

    async def _stream_compaction(
        *,
        trigger: str,
        force: bool = False,
    ) -> AsyncIterator[tuple[StreamEvent, UsageSnapshot | None]]:
        nonlocal last_compaction_result
        progress_queue: asyncio.Queue[CompactProgressEvent] = asyncio.Queue()

        async def _progress(event: CompactProgressEvent) -> None:
            await progress_queue.put(event)

        task = asyncio.create_task(
            auto_compact_if_needed(
                messages,
                api_client=context.api_client,
                model=context.model,
                system_prompt=context.system_prompt,
                state=compact_state,
                progress_callback=_progress,
                force=force,
                trigger=trigger,
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
        last_compaction_result = await task
        return
    
    turn_count = 0
    done_reminder_count = 0
    while context.max_turns is None or turn_count < context.max_turns:
        turn_count += 1
        if effective_max_tokens != context.max_tokens and not reported_token_clamp:
            reported_token_clamp = True
            yield StatusEvent(
                message=(
                    f"Requested max_tokens={context.max_tokens} exceeds the safe per-request "
                    f"output cap; using {effective_max_tokens}."
                )
            ), None
        # --- auto-compact check before calling the model ---------------
        async for event, usage in _stream_compaction(trigger="auto"):
            yield event, usage
        messages, was_compacted = last_compaction_result
        if messages is not external_messages:
            external_messages[:] = messages
            messages = external_messages
        # ---------------------------------------------------------------

        if was_compacted:
            todo_msg = context.tool_metadata['todo_store'].format_for_injection()
            if todo_msg is not None:
                messages.append(ConversationMessage.from_user_content(todo_msg))
            # Clear the file-read cache so the agent re-reads files with fresh
            # content after the context has been compacted.
            context.tool_metadata.pop(ToolMetadataKey.FILE_READ_CACHE.value, None)

        async for event in _preprocess_images_in_messages(messages, context):
            yield event, None

        messages[:] = normalize_messages_for_api(messages)

        final_message: ConversationMessage | None = None
        usage = UsageSnapshot()
        stop_reason: str | None = None

        try:
            async for event in context.api_client.stream_message(
                ApiMessageRequest(
                    model=context.model,
                    messages=messages,
                    system_prompt=context.system_prompt,
                    max_tokens=effective_max_tokens,
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
                    final_message = event.message
                    usage = event.usage
                    stop_reason = event.stop_reason
        except Exception as exc:
            # str(exc) can be empty for some exception types; use repr as fallback
            error_msg = str(exc) or repr(exc)
            logger.event(
                "query_api_exception",
                session_id=session_id,
                model=context.model,
                turn_count=turn_count,
                error=error_msg,
                exc_type=type(exc).__name__,
                exc_repr=repr(exc),
            )
            if _is_completion_token_limit_error(exc):
                supported_limit = _extract_completion_token_limit(exc)
                if supported_limit is not None and effective_max_tokens > supported_limit:
                    previous_max_tokens = effective_max_tokens
                    effective_max_tokens = supported_limit
                    yield StatusEvent(
                        message=(
                            f"Model rejected max_tokens={previous_max_tokens}; "
                            f"retrying with provider limit {effective_max_tokens}."
                        )
                    ), None
                    turn_count = max(0, turn_count - 1)
                    continue
            if not reactive_compact_attempted and _is_prompt_too_long_error(exc):
                reactive_compact_attempted = True
                yield StatusEvent(message=REACTIVE_COMPACT_STATUS_MESSAGE), None
                async for event, usage in _stream_compaction(trigger="reactive", force=True):
                    yield event, usage
                messages, was_compacted = last_compaction_result
                if messages is not external_messages:
                    external_messages[:] = messages
                    messages = external_messages
                messages[:] = normalize_messages_for_api(messages)
                if was_compacted:
                    continue
            if "connect" in error_msg.lower() or "timeout" in error_msg.lower() or "network" in error_msg.lower():
                yield ErrorEvent(message=f"Network error: {error_msg}. Check your internet connection and try again."), None
            else:
                yield ErrorEvent(message=f"API error: {error_msg}"), None
            return

        if final_message is None:
            raise RuntimeError("Model stream finished without a final message")
        
        # TODO: call `post_api_request` hook

        coordinator_context_message: ConversationMessage | None = None
        if context.system_prompt.startswith("You are a **coordinator**."):
            if messages and messages[-1].role == "user" and messages[-1].text.startswith("# Coordinator User Context"):
                coordinator_context_message = messages.pop()

        messages.append(final_message)
        logger.event(
            "api_message_complete",
            session_id=session_id,
            model=context.model,
            turn_count=turn_count,
            stop_reason=stop_reason,
            text_length=len(final_message.text),
            tool_use_count=len(final_message.tool_uses),
            message_count=len(messages),
        )
        yield AssistantTurnComplete(message=final_message, usage=usage), usage

        if coordinator_context_message is not None:
            messages.append(coordinator_context_message)

        if not final_message.tool_uses:
            if context.require_explicit_done and done_reminder_count < _MAX_DONE_REMINDER_RETRIES:
                # In full_auto mode, the model must call done() explicitly.
                # Inject a reminder and continue the loop.
                done_reminder_count += 1
                logger.event(
                    "done_reminder_injected",
                    session_id=session_id,
                    model=context.model,
                    turn_count=turn_count,
                    reminder_count=done_reminder_count,
                )
                messages.append(
                    ConversationMessage.from_user_text(
                        "<openharness-internal:done-reminder>\n"
                        "You stopped without calling the done() tool. "
                        "If the task is complete, you MUST call done(message=...) to signal completion. "
                        "If you still have work to do, continue using tools."
                    )
                )
                continue
            logger.event(
                "query_turn_finished_without_tool_use",
                session_id=session_id,
                model=context.model,
                turn_count=turn_count,
                text_length=len(final_message.text),
                message_count=len(messages),
            )
            if context.hook_executor is not None:
                await context.hook_executor.execute(
                    HookEvent.STOP,
                    {
                        "event": HookEvent.STOP.value,
                        "stop_reason": "tool_uses_empty",
                    },
                )
            return

        tool_calls = final_message.tool_uses

        # Enforce: done() must be the sole tool call. If mixed with others,
        # skip execution entirely and return an error for the done call.
        done_indices = [i for i, tc in enumerate(tool_calls) if tc.name == DONE_TOOL_NAME]
        if done_indices and len(tool_calls) > 1:
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
                    # Execute the non-done tools normally
                    yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
                    result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
                    yield ToolExecutionCompleted(
                        tool_name=tc.name,
                        output=result.content,
                        is_error=result.is_error,
                    ), None
                    tool_results.append(result)
            messages.append(ConversationMessage(role="user", content=tool_results))
            logger.event(
                "done_tool_rejected_mixed",
                session_id=session_id,
                model=context.model,
                turn_count=turn_count,
                total_tools=len(tool_calls),
            )
            continue

        if len(tool_calls) == 1:
            # Single tool: sequential (stream events immediately)
            tc = tool_calls[0]
            yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None
            result = await _execute_tool_call(context, tc.name, tc.id, tc.input)
            yield ToolExecutionCompleted(
                tool_name=tc.name,
                output=result.content,
                is_error=result.is_error,
            ), None
            tool_results = [result]
        else:
            # Multiple tools: execute concurrently, emit events after
            for tc in tool_calls:
                yield ToolExecutionStarted(tool_name=tc.name, tool_input=tc.input), None

            async def _run(tc):
                return await _execute_tool_call(context, tc.name, tc.id, tc.input)

            # Use return_exceptions=True so a single failing tool does not abandon
            # its siblings as cancelled coroutines and leave the conversation with
            # un-replied tool_use blocks (Anthropic's API rejects the next request
            # on the session if any tool_use is missing a matching tool_result).
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

        messages.append(ConversationMessage(role="user", content=tool_results))
        repair_prompt = build_internal_tool_name_repair_prompt(context.tool_metadata)
        if repair_prompt is not None:
            messages.append(repair_prompt)
        logger.event(
            "tool_results_appended",
            session_id=session_id,
            model=context.model,
            turn_count=turn_count,
            tool_result_count=len(tool_results),
            error_count=sum(1 for result in tool_results if result.is_error),
            message_count=len(messages),
        )

        # Explicit done() detection: done is the sole tool call — exit the loop.
        if len(tool_calls) == 1 and tool_calls[0].name == DONE_TOOL_NAME:
            logger.event(
                "query_done_tool_called",
                session_id=session_id,
                model=context.model,
                turn_count=turn_count,
                message_count=len(messages),
            )
            if context.hook_executor is not None:
                await context.hook_executor.execute(
                    HookEvent.STOP,
                    {
                        "event": HookEvent.STOP.value,
                        "stop_reason": "done_tool_called",
                    },
                )
            return

    if context.max_turns is not None:
        raise MaxTurnsExceeded(context.max_turns)
    raise RuntimeError("Query loop exited without a max_turns limit or final response")


async def _execute_tool_call(
    context: QueryContext,
    tool_name: str,
    tool_use_id: str,
    tool_input: dict[str, object],
) -> ToolResultBlock:
    doom_loop = should_block_tool_call(context.tool_metadata, tool_name, tool_input)
    if doom_loop.blocked:
        logger.warning("blocked repeated failing tool call: %s id=%s", tool_name, tool_use_id)
        return build_doom_loop_result(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            reason=doom_loop.reason,
        )

    result = await ToolExecutionPipeline(_default_tool_pipeline_stages()).run(
        context=context,
        tool_name=tool_name,
        tool_use_id=tool_use_id,
        tool_input=tool_input,
    )
    record_tool_call_result(context.tool_metadata, tool_name, tool_input, result)
    return result


def _default_tool_pipeline_stages() -> tuple[ToolPipelineStage, ...]:
    return (
        ToolPipelineStage("resolve_tool", _resolve_tool_stage),
        ToolPipelineStage("pre_hook", _pre_tool_hook_stage),
        ToolPipelineStage("validate_input", _validate_tool_input_stage),
        ToolPipelineStage("check_permission", _check_tool_permission_stage),
        ToolPipelineStage("execute_tool", _execute_tool_stage),
        ToolPipelineStage("normalize_result", _normalize_tool_result_stage),
        ToolPipelineStage("update_metadata", _update_tool_metadata_stage),
        ToolPipelineStage("post_hook", _post_tool_hook_stage),
    )


async def _resolve_tool_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    logger.debug("tool_call start: %s id=%s", state.tool_name, state.tool_use_id)

    repair = repair_tool_name(state.tool_name, context.tool_registry)
    state.repair = repair
    if repair.resolved_name is None:
        logger.warning("unknown tool: %s", state.tool_name)
        state.result = build_invalid_tool_result(
            tool_use_id=state.tool_use_id,
            requested_name=state.tool_name,
            available_names=repair.available_names,
            suggestions=repair.suggestions,
        )
        state.stop = True
        return state
    if repair.repaired:
        _remember_tool_name_repair(
            context.tool_metadata,
            requested_name=repair.requested_name,
            resolved_name=repair.resolved_name,
            reason=repair.reason,
            tool_use_id=state.tool_use_id,
        )
        logger.info("repaired tool name: %s -> %s (%s)", state.tool_name, repair.resolved_name, repair.reason)
        state.tool_name = repair.resolved_name

    state.tool = context.tool_registry.get(state.tool_name)
    if state.tool is None:
        logger.warning("unknown tool: %s", state.tool_name)
        state.result = build_invalid_tool_result(
            tool_use_id=state.tool_use_id,
            requested_name=state.tool_name,
            available_names=repair.available_names,
            suggestions=repair.suggestions,
        )
        state.stop = True
    return state


async def _pre_tool_hook_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    if context.hook_executor is not None:
        pre_hooks = await context.hook_executor.execute(
            HookEvent.PRE_TOOL_USE,
            {"tool_name": state.tool_name, "tool_input": state.tool_input, "event": HookEvent.PRE_TOOL_USE.value},
        )
        if pre_hooks.blocked:
            state.result = ToolResultBlock(
                tool_use_id=state.tool_use_id,
                content=pre_hooks.reason or f"pre_tool_use hook blocked {state.tool_name}",
                is_error=True,
            )
            state.stop = True
    return state


async def _validate_tool_input_stage(state: ToolPipelineState) -> ToolPipelineState:
    try:
        state.parsed_input = state.tool.input_model.model_validate(state.tool_input)
    except Exception as exc:
        logger.warning("invalid input for %s: %s", state.tool_name, exc)
        hint = _build_validation_error_hint(state.tool, state.tool_input, exc)
        state.result = ToolResultBlock(
            tool_use_id=state.tool_use_id,
            content=hint,
            is_error=True,
        )
        state.stop = True
    return state


async def _check_tool_permission_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    state.permission_file_path = _resolve_permission_file_path(context.cwd, state.tool_input, state.parsed_input)
    state.permission_command = _extract_permission_command(state.tool_input, state.parsed_input)
    logger.debug("permission check: %s read_only=%s path=%s cmd=%s", 
                 state.tool_name, state.tool.is_read_only(state.parsed_input), state.permission_file_path,
                 state.permission_command and state.permission_command[:80])
    decision = context.permission_checker.evaluate(
        state.tool_name,
        is_read_only=state.tool.is_read_only(state.parsed_input),
        file_path=state.permission_file_path,
        command=state.permission_command,
    )
    if not decision.allowed:
        if decision.requires_confirmation and context.permission_prompt is not None:
            async with _permission_prompt_lock(context):
                decision = context.permission_checker.evaluate(
                    state.tool_name,
                    is_read_only=state.tool.is_read_only(state.parsed_input),
                    file_path=state.permission_file_path,
                    command=state.permission_command,
                )
                if decision.allowed:
                    logger.debug("permission allowed after recheck for %s: %s", state.tool_name, decision.reason)
                elif decision.requires_confirmation:
                    logger.debug("permission prompt for %s: %s", state.tool_name, decision.reason)
                    if context.hook_executor is not None:
                        await context.hook_executor.execute(
                            HookEvent.NOTIFICATION,
                            {
                                "event": HookEvent.NOTIFICATION.value,
                                "notification_type": "permission_prompt",
                                "tool_name": state.tool_name,
                                "reason": decision.reason,
                            },
                        )
                    confirmed = await context.permission_prompt(state.tool_name, decision.reason)
                    reply = _normalize_permission_reply(confirmed)
                    if reply == "reject":
                        logger.debug("permission denied by user for %s", state.tool_name)
                        state.result = ToolResultBlock(
                            tool_use_id=state.tool_use_id,
                            content=decision.reason or f"Permission denied for {state.tool_name}",
                            is_error=True,
                        )
                        state.stop = True
                        return state
                    if reply == "always":
                        context.permission_checker.remember_allow(decision)
                else:
                    logger.debug("permission blocked after recheck for %s: %s", state.tool_name, decision.reason)
                    state.result = ToolResultBlock(
                        tool_use_id=state.tool_use_id,
                        content=decision.reason or f"Permission denied for {state.tool_name}",
                        is_error=True,
                    )
                    state.stop = True
                    return state
        else:
            logger.debug("permission blocked for %s: %s", state.tool_name, decision.reason)
            state.result = ToolResultBlock(
                tool_use_id=state.tool_use_id,
                content=decision.reason or f"Permission denied for {state.tool_name}",
                is_error=True,
            )
            state.stop = True
    return state


async def _execute_tool_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    logger.event(
        "tool_execution_request",
        tool_name=state.tool_name,
        tool_use_id=state.tool_use_id,
        tool_input=state.tool_input,
        is_read_only=state.tool.is_read_only(state.parsed_input),
        file_path=state.permission_file_path,
        command=state.permission_command,
    )
    t0 = time.monotonic()
    try:
        state.raw_result = await state.tool.execute(
            state.parsed_input,
            ToolExecutionContext(
                cwd=context.cwd,
                metadata={
                    "tool_registry": context.tool_registry,
                    "ask_user_prompt": context.ask_user_prompt,
                    **(context.tool_metadata or {}),
                },
                hook_executor=context.hook_executor,
            ),
        )
    except Exception as exc:
        logger.exception(
            "tool execution raised: name=%s id=%s",
            state.tool_name,
            state.tool_use_id,
            exc_info=exc,
        )
        state.result = ToolResultBlock(
            tool_use_id=state.tool_use_id,
            content=f"Tool {state.tool_name} failed: {type(exc).__name__}: {exc}",
            is_error=True,
        )
        state.stop = True
        return state
    elapsed = time.monotonic() - t0
    logger.debug("executed %s in %.2fs err=%s output_len=%d", 
                 state.tool_name, elapsed, state.raw_result.is_error, len(state.raw_result.output or ""))
    return state


async def _normalize_tool_result_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    normalized = TextToolResultNormalizer().normalize(
        tool_name=state.tool_name,
        tool_use_id=state.tool_use_id,
        output=state.raw_result.output or "",
    )
    state.artifact_path = normalized.artifact_path
    if normalized.artifact_path is not None:
        _remember_active_artifact(context.tool_metadata, str(normalized.artifact_path))
    state.result = ToolResultBlock(
        tool_use_id=state.tool_use_id,
        content=normalized.inline_content,
        is_error=state.raw_result.is_error,
    )
    return state


async def _update_tool_metadata_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    if state.result is None:
        raise RuntimeError("tool metadata stage requires a normalized tool result")
    _record_tool_carryover(
        context,
        tool_name=state.tool_name,
        tool_input=state.tool_input,
        tool_output=state.result.content,
        tool_result_metadata=state.raw_result.metadata,
        is_error=state.result.is_error,
        resolved_file_path=state.permission_file_path,
    )
    return state


async def _post_tool_hook_stage(state: ToolPipelineState) -> ToolPipelineState:
    context: QueryContext = state.context
    if state.result is None:
        raise RuntimeError("post hook stage requires a normalized tool result")
    if context.hook_executor is not None:
        await context.hook_executor.execute(
            HookEvent.POST_TOOL_USE,
            {
                "tool_name": state.tool_name,
                "tool_input": state.tool_input,
                "tool_output": state.result.content,
                "tool_is_error": state.result.is_error,
                "event": HookEvent.POST_TOOL_USE.value,
            },
        )
    return state


def _build_validation_error_hint(
    tool,
    tool_input: dict[str, object],
    exc: Exception,
) -> str:
    """Construct a model-friendly error hint when tool input validation fails.

    Local LLMs (especially via vLLM's qwen3_xml parser) can return empty or
    malformed tool arguments when the model omits required parameters or the
    XML parser strips them.  This hint tells the model exactly what went wrong
    and what fields are expected so it can retry the call in the next turn.
    """
    schema = tool.input_model.model_json_schema()
    required = schema.get("required", [])
    properties = schema.get("properties", {})

    # Detect truly empty / near-empty input (the common qwen3_xml failure mode)
    is_empty = (
        not tool_input
        or all(v in (None, "", []) for v in tool_input.values())
    )

    lines: list[str] = [f"Invalid input for {tool.name}."]
    if is_empty:
        lines.append(
            "The tool arguments you provided were empty or missing. "
            "This usually happens when the model output did not include "
            "the required parameters (e.g. due to an XML parsing issue). "
            "Please retry this tool call and make sure to provide ALL "
            "required arguments explicitly."
        )
    else:
        lines.append(f"Validation error: {exc}")

    if required:
        lines.append(f"Required parameters: {', '.join(required)}")

    # Show a short parameter cheat-sheet (up to 6 fields)
    for prop_name, prop_info in list(properties.items())[:6]:
        desc = prop_info.get("description", "")
        if desc:
            lines.append(f"  - {prop_name}: {desc}")
        else:
            lines.append(f"  - {prop_name}")

    lines.append(
        "Please correct the arguments and call the tool again. "
        "Do not explain the error to the user; just retry with valid arguments."
    )
    return "\n".join(lines)


def _normalize_permission_reply(reply: object) -> str:
    if reply is True:
        return "once"
    if reply is False or reply is None:
        return "reject"
    normalized = str(reply).strip().lower()
    if normalized in {"once", "always", "reject"}:
        return normalized
    if normalized in {"allow", "allowed", "yes", "y"}:
        return "once"
    if normalized in {"deny", "denied", "no", "n"}:
        return "reject"
    return "reject"


def _resolve_permission_file_path(
    cwd: Path,
    raw_input: dict[str, object],
    parsed_input: object,
) -> str | None:
    # Tools involving file reading or modification use 
    # one of these three keys('file_path', 'path', 'root') 
    # in their input schema, so check them in order
    for key in ("file_path", "path", "root"):
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())

    for attr in ("file_path", "path", "root"):
        value = getattr(parsed_input, attr, None)
        if isinstance(value, str) and value.strip():
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = cwd / path
            return str(path.resolve())

    return None


def _extract_permission_command(
    raw_input: dict[str, object],
    parsed_input: object,
) -> str | None:
    value = raw_input.get("command")
    if isinstance(value, str) and value.strip():
        return value

    value = getattr(parsed_input, "command", None)
    if isinstance(value, str) and value.strip():
        return value

    return None
