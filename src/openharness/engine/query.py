"""Core tool-aware query loop."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, TYPE_CHECKING

from openharness.api.client import SupportsStreamingMessages
from openharness.api.usage import UsageSnapshot
from openharness.engine.messages import ConversationMessage, ToolResultBlock
from openharness.engine.stream_events import StreamEvent
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
from openharness.utils.log import get_logger

if TYPE_CHECKING:
    from openharness.permissions.approvals import ApprovalCoordinator

AUTO_COMPACT_STATUS_MESSAGE = "Auto-compacting conversation memory to keep things fast and focused."
REACTIVE_COMPACT_STATUS_MESSAGE = "Prompt too long; compacting conversation memory and retrying."
MAX_SAFE_COMPLETION_TOKENS = 128_000

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
INTERNAL_TRUNCATION_RECOVERY_PREFIX = "<openharness-internal:truncation-recovery>"


def _bounded_completion_tokens(max_tokens: int, context_window_tokens: int | None = None) -> int:
    """Return a conservative per-request output token cap."""
    limit = MAX_SAFE_COMPLETION_TOKENS
    if context_window_tokens is not None and context_window_tokens > 0:
        limit = min(limit, int(context_window_tokens))
    return max(1, min(int(max_tokens), limit))


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
    approval_coordinator: ApprovalCoordinator | None = None

    def __post_init__(self) -> None:
        if self.approval_coordinator is None:
            from openharness.permissions.approvals import ApprovalCoordinator, ApprovalRequest, PromptFn

            prompt_fn: PromptFn | None = None
            if self.permission_prompt is not None:
                _perm = self.permission_prompt

                async def _legacy_prompt(request: ApprovalRequest) -> str:
                    return await _perm(request.tool_name, request.reason)  # type: ignore[arg-type]

                prompt_fn = _legacy_prompt

            self.approval_coordinator = ApprovalCoordinator(
                self.permission_checker,
                prompt_fn=prompt_fn,
            )


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

    The loop is decomposed into stages (defined in turn_stages.py).
    Each stage yields events and signals the orchestrator via TurnState.action.
    """
    from openharness.engine.turn_stages import (
        DEFAULT_TURN_STAGES,
        TurnAction,
        TurnState,
    )
    from openharness.services.compact import AutoCompactState

    # Initialize turn state
    state = TurnState(
        context=context,
        external_messages=messages,
        messages=messages,
        effective_max_tokens=_bounded_completion_tokens(
            context.max_tokens,
            context.context_window_tokens,
        ),
        compact_state=AutoCompactState(),
        last_compaction_result=(messages, False),
    )
    if isinstance(context.tool_metadata, dict):
        raw_session_id = context.tool_metadata.get("session_id")
        if isinstance(raw_session_id, str) and raw_session_id:
            state.session_id = raw_session_id

    # Main loop
    while context.max_turns is None or state.turn_count < context.max_turns:
        state.turn_count += 1
        state.reset_turn()

        for stage in DEFAULT_TURN_STAGES:
            async for event_tuple in stage(state):
                yield event_tuple
            if state.action != TurnAction.PROCEED:
                break

        if state.action == TurnAction.STOP:
            return
        if state.action == TurnAction.RETRY_TURN:
            state.turn_count = max(0, state.turn_count - 1)
            continue
        # NEXT_TURN and PROCEED both continue to next iteration

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

    is_read_only = state.tool.is_read_only(state.parsed_input)
    coordinator = context.approval_coordinator
    if coordinator is not None:
        preview = await state.tool.compute_preview(state.parsed_input, context.cwd)
        decision = await coordinator.authorize_tool(
            state.tool_name,
            is_read_only=is_read_only,
            file_path=state.permission_file_path,
            command=state.permission_command,
            preview=preview,
        )
    else:
        decision = context.permission_checker.evaluate(
            state.tool_name,
            is_read_only=is_read_only,
            file_path=state.permission_file_path,
            command=state.permission_command,
        )

    if not decision.allowed:
        logger.debug("permission blocked for %s: %s", state.tool_name, decision.reason)
        state.result = ToolResultBlock(
            tool_use_id=state.tool_use_id,
            content=decision.reason or f"Permission denied for {state.tool_name}",
            is_error=True,
        )
        state.stop = True
    else:
        logger.debug("permission granted for %s: %s", state.tool_name, decision.reason)
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
                approval_coordinator=context.approval_coordinator,
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
        result_metadata=dict(state.raw_result.metadata or {}),
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
