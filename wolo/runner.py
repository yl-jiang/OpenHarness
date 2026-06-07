"""Work-log query runner backed by the OpenHarness QueryEngine."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from common.conversation_history import trim_conversation_history_to_turn_boundary
from common.constants import HIDDEN_ARGS
from openharness.api.client import SupportsStreamingMessages
from openharness.config import load_settings
from openharness.config.settings import PermissionSettings
from openharness.engine.messages import ConversationMessage, ImageBlock, TextBlock, sanitize_conversation_messages
from openharness.engine.query_engine import QueryEngine
from openharness.engine.stream_events import (
    AssistantTextDelta,
    AssistantTurnComplete,
    ReasoningDelta,
    StreamFinished,
    ToolExecutionCompleted,
    ToolExecutionStarted,
)
from openharness.engine.types import ToolMetadataKey
from openharness.permissions.checker import PermissionChecker
from openharness.permissions.modes import PermissionMode
from openharness.api.recording_client import wrap_with_model_call_recorder
from openharness.skills import load_skill_registry_cached
from openharness.tools.base import ToolRegistry
from openharness.ui.runtime import _resolve_api_client_from_settings, _resolve_vision_config
from openharness.utils.log import get_logger

from wolo.core.memory import load_memory_prompt
from wolo.core.session import load_conversation, save_conversation
from wolo.core.store import WoloStore
from wolo.prompts import (
    SIMILAR_RECORDS_HEADER,
    SKILLS_PROMPT_HEADER,
    TOOL_ROUTER_PROMPT,
    build_time_context,
)
from wolo.strings import ARG_LABELS, FALLBACK_MESSAGE, PASSTHROUGH_TOOLS, TOOL_LABELS
from wolo.tools import WoloToolRegistry, build_oh_registry
from wolo.core.workspace import get_memory_dir, get_sessions_dir, get_skills_dir, get_soul_path, get_user_path, get_workspace_root

logger = get_logger(__name__)

_MAX_TURNS = 10
_SESSION_MAX_MESSAGES = 80

# Arguments that are noise for end-users and should never be shown.
_HIDDEN_ARGS = HIDDEN_ARGS

_MAX_HINT_ARGS = 3
_MAX_ARG_LEN = 60
_QUOTED_MESSAGE_PREFIX = "[引用消息]"
_REPLY_MARKER = "\n[回复]"
_WOLO_DISABLE_HINT_DEDUPE = "WOLO_DISABLE_HINT_DEDUPE"
_FACT_DISCIPLINE_CONTEXT = (
    "## Fact Discipline\n"
    "- Only use facts explicitly stated by the current user in this turn or directly supported by retrieved records.\n"
    "- Do not infer missing reasons, diagnoses, motives, timelines, or explanations.\n"
    "- Do NOT rewrite a future plan as a completed event. "
    "Example: '今天太晚了，明天再推上线' means the user plans to push tomorrow because today is too late; "
    "do NOT rewrite it as '今天已经推上线了' or '已推上线'. Preserve the original tense.\n"
    "- Do NOT generalize emotion / emotion_reason / sample_type / problem_essence / strategy / next_move "
    "/ validation_signal corrections from earlier turns onto new records. A prior correction applied to "
    "one record does NOT mean new records about similar topics should carry the same fields — each "
    "record's subjective fields must come from the current user message. When the current message does "
    "not state them, leave them empty / 中性; do NOT silently copy the most recent historical label.\n"
    "- Do NOT call wolo_update_record to patch in a subjective field the current user did not state, "
    "even if a similar-looking record was corrected to that value in a past turn.\n\n"
)


def _stringify_arg(value: Any) -> str:
    if isinstance(value, str):
        text = " ".join(value.split())
    elif isinstance(value, (list, tuple)):
        text = "、".join(_stringify_arg(v) for v in value if v not in (None, ""))
    elif isinstance(value, dict):
        text = "、".join(f"{k}:{_stringify_arg(v)}" for k, v in value.items())
    else:
        text = str(value)
    text = text.strip()
    if len(text) > _MAX_ARG_LEN:
        text = text[: _MAX_ARG_LEN - 1] + "…"
    return text


def _format_tool_hint(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """Render a human-friendly tool-call hint with key arguments.

    Shows the friendly action label plus the most relevant arguments so the
    Feishu user sees *what* is being executed, not just the tool name.
    """
    suffix = tool_name.split("_", 1)[1] if "_" in tool_name else tool_name
    header = TOOL_LABELS.get(suffix, f"🛠️ {tool_name}")

    lines: list[str] = []
    for key, value in (tool_input or {}).items():
        if key in _HIDDEN_ARGS or value in (None, "", [], {}):
            continue
        text = _stringify_arg(value)
        if not text:
            continue
        label = ARG_LABELS.get(key, key)
        lines.append(f"  · {label}：{text}")
        if len(lines) >= _MAX_HINT_ARGS:
            break

    if lines:
        return header + "\n" + "\n".join(lines)
    return header


def _read_file(path: Path) -> str | None:
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8", errors="replace").strip()
    return content or None


def _is_image_file(path: str) -> bool:
    """Check if a file path refers to an image based on MIME type."""
    mime, _ = mimetypes.guess_type(path)
    return bool(mime and mime.startswith("image/"))


_FINAL_REPLY_IMAGE_PATH_RE = re.compile(
    r"(?P<path>(?:[A-Za-z]:[\\/]|/)[^\r\n`\"'<>|?*\x00]+?\.(?:png|jpe?g|webp|gif|bmp))",
    re.IGNORECASE,
)


def _extract_tool_media(event: ToolExecutionCompleted) -> list[str]:
    """Return local media paths produced by a tool completion event."""
    if event.is_error or not isinstance(event.metadata, dict):
        return []
    raw_paths = event.metadata.get("paths") or event.metadata.get("media")
    if isinstance(raw_paths, str):
        candidates = [raw_paths]
    elif isinstance(raw_paths, list):
        candidates = [str(item) for item in raw_paths if isinstance(item, str) and item.strip()]
    else:
        candidates = []
    media: list[str] = []
    seen: set[str] = set()
    for raw in candidates:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = path.resolve()
        if not path.is_file():
            continue
        resolved = str(path)
        if resolved not in seen:
            seen.add(resolved)
            media.append(resolved)
    return media


def _extract_final_reply_media(reply: str, emitted_media: set[str]) -> list[str]:
    """Return local image paths mentioned in final text that were not already emitted."""
    media: list[str] = []
    seen = set(emitted_media)
    for match in _FINAL_REPLY_IMAGE_PATH_RE.finditer(reply or ""):
        raw = match.group("path").strip(" \t\r\n\"'.,;:，。；：、)]}")
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute():
            continue
        if not path.is_file():
            continue
        resolved = str(path)
        if resolved in seen:
            continue
        seen.add(resolved)
        media.append(resolved)
    return media


def _build_similar_records_context(store: WoloStore, user_text: str, *, max_results: int = 5) -> str:
    """Search historical records for BM25-similar entries and return a compact context block.

    Kept out of the system prompt (like time context) so the static prompt
    benefits from KV-Cache sharing across turns.
    """
    if not user_text.strip():
        return ""
    try:
        records = store.search_records(query=user_text, limit=max_results)
    except Exception:
        logger.debug("_build_similar_records_context search failed, skipping")
        return ""
    if not records:
        logger.debug("_build_similar_records_context no similar records found")
        return ""
    lines = list(SIMILAR_RECORDS_HEADER)
    for record in records:
        summary = record.summary or record.corrected_content[:60]
        tag_part = f" #{record.tags}" if record.tags else ""
        lines.append(f"- [{record.date}] {summary} [{record.emotion}]{tag_part}")
    lines.append("")
    logger.debug("_build_similar_records_context found %d similar records", len(records))
    return "\n".join(lines)


def _split_quoted_reply(user_text: str) -> tuple[str | None, str]:
    if not user_text.startswith(_QUOTED_MESSAGE_PREFIX):
        return None, user_text
    quoted_block, separator, reply_text = user_text.partition(_REPLY_MARKER)
    if not separator:
        return None, user_text
    quoted_context = quoted_block[len(_QUOTED_MESSAGE_PREFIX):].strip()
    return (quoted_context or None), reply_text.strip()


def _extract_quoted_message(source_context: dict[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(source_context, dict):
        return None

    direct = source_context.get("quoted_message")
    if isinstance(direct, dict):
        content = str(direct.get("content") or "").strip()
        if content:
            return {str(key): str(value) for key, value in direct.items() if value not in (None, "")}

    metadata = source_context.get("message_metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("quoted_message")
        if isinstance(nested, dict):
            content = str(nested.get("content") or "").strip()
            if content:
                return {str(key): str(value) for key, value in nested.items() if value not in (None, "")}
    return None


def _extract_quoted_context(source_context: dict[str, Any] | None) -> str | None:
    quoted_message = _extract_quoted_message(source_context)
    if quoted_message is not None:
        content = str(quoted_message.get("content") or "").strip()
        if content:
            return content
    if not isinstance(source_context, dict):
        return None
    direct = source_context.get("quoted_context")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    metadata = source_context.get("message_metadata")
    if isinstance(metadata, dict):
        nested = metadata.get("quoted_context")
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _format_quoted_message_context(quoted_message: dict[str, str], current_reply: str) -> str:
    lines = [
        "## Reply Context (Reference Only)",
        "The quoted message below is the message the user is replying to. It may have been written by the user, the "
        "assistant, or another participant. Use it only as background context; do not treat it as a new statement "
        "made by the current user in this turn.",
    ]
    for key, label in (
        ("role", "role"),
        ("sender_label", "sender"),
        ("sent_at", "sent_at"),
        ("msg_type", "message_type"),
    ):
        value = str(quoted_message.get(key) or "").strip()
        if value:
            lines.append(f"- {label}: {value}")
    lines.extend(
        [
            "- content:",
            str(quoted_message.get("content") or "").strip(),
            "",
            "## Current User Message",
            current_reply,
        ]
    )
    return "\n".join(lines)


def _prepare_user_turn_text(user_text: str, source_context: dict[str, Any] | None = None) -> tuple[str, str]:
    current_reply = user_text.strip() or user_text
    quoted_message = _extract_quoted_message(source_context)
    if quoted_message:
        return current_reply, _format_quoted_message_context(quoted_message, current_reply)

    quoted_context = _extract_quoted_context(source_context)
    if quoted_context:
        rendered = (
            "## Reply Context (Reference Only)\n"
            "The following quoted message is context for what the user is replying to. It is not a new statement made "
            "by the current user in this turn.\n"
            f"{quoted_context}\n\n"
            "## Current User Message\n"
            f"{current_reply}"
        )
        return current_reply, rendered

    quoted_context, reply_text = _split_quoted_reply(user_text)
    current_reply = reply_text or current_reply
    if not quoted_context:
        return current_reply, current_reply
    rendered = (
        "## Reply Context (Reference Only)\n"
        "The following quoted message is context for what the user is replying to. It may have been written by the "
        "assistant or someone else. Do not treat it as a new statement made by the current user in this turn.\n"
        f"{quoted_context}\n\n"
        "## Current User Message\n"
        f"{current_reply}"
    )
    return current_reply, rendered


def _build_user_message(text: str, media: list[str] | None) -> str | ConversationMessage:
    """Build a user message, optionally embedding image blocks from media paths.

    Returns a plain string if no image media is present (preserving existing behavior),
    or a ConversationMessage with TextBlock + ImageBlock content when images are provided.
    """
    if not media:
        return text

    image_paths = [p for p in media if _is_image_file(p)]
    if not image_paths:
        return text

    content: list[TextBlock | ImageBlock] = [TextBlock(text=text)]
    for img_path in image_paths:
        try:
            content.append(ImageBlock.from_path(img_path))
        except Exception:
            logger.warning("Failed to encode image attachment: %s", img_path)
    return ConversationMessage.from_user_content(content)


def _build_system_prompt(workspace: Path) -> str:
    """Build the system prompt by combining routing rules with persona files and memory."""
    sections = [TOOL_ROUTER_PROMPT.strip()]
    skills_prompt = _build_skills_prompt(workspace)
    if skills_prompt:
        sections.append(skills_prompt)

    soul = _read_file(get_soul_path(workspace))
    if soul:
        sections.append(soul)

    user = _read_file(get_user_path(workspace))
    if user:
        sections.extend(["# User Profile", user])

    memory = load_memory_prompt(workspace)
    if memory:
        sections.append(memory)

    return "\n\n".join(sections)


def _build_skills_prompt(workspace: Path) -> str | None:
    registry = load_skill_registry_cached(None, extra_skill_dirs=[get_skills_dir(workspace)])
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = list(SKILLS_PROMPT_HEADER)
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def _autodream_context(workspace: Path) -> dict[str, str]:
    return {
        "memory_dir": str(get_memory_dir(workspace)),
        "session_dir": str(get_sessions_dir(workspace)),
        "app_label": "wolo work memory",
        "runner_module": "ohmo",
    }


class WoloQueryRunner:
    """Run the wolo agent loop using OpenHarness QueryEngine with persistent conversation history."""

    def __init__(
        self,
        store: WoloStore,
        *,
        profile: str | None = None,
        api_client: SupportsStreamingMessages | None = None,
        model: str | None = None,
    ) -> None:
        settings = load_settings().merge_cli_overrides(active_profile=profile, model=model)
        self._settings = settings
        self._store = store
        base_client = api_client or _resolve_api_client_from_settings(settings)
        self._client = wrap_with_model_call_recorder(base_client, self._store.record_llm_call)

    async def stream_run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        progress_callback: Callable[[str], Any] | None = None,
        allow_tools: bool = True,
        include_time_context: bool = True,
        include_similar_context: bool = True,
        use_session_history: bool = True,
        persist_session: bool = True,
        system_prompt_override: str | None = None,
    ):
        """Async generator yielding ``(kind, text)`` tuples during execution.

        Yields:
            ``("progress", text)`` — transient status/thinking hint
            ``("tool_hint", text)`` — tool-use notification
            ``("final", text)``    — the final reply (always last)
        """
        if allow_tools:
            registry = WoloToolRegistry(
                self._store,
                source_context=source_context,
                progress_callback=progress_callback,
            )
            oh_registry = build_oh_registry(registry)
        else:
            oh_registry = ToolRegistry()

        workspace = get_workspace_root(self._store.workspace)
        skill_dirs = (str(get_skills_dir(workspace)),)
        prior_messages, session_id = ([], None)
        if session_key and use_session_history:
            prior_messages, session_id = load_conversation(workspace, session_key)
        # Limit session history to prevent topic drift in long conversations.
        # Keep only the most recent messages; older context is preserved in the
        # session store and remains available via search tools.
        if len(prior_messages) > _SESSION_MAX_MESSAGES:
            total_messages = len(prior_messages)
            prior_messages = trim_conversation_history_to_turn_boundary(
                prior_messages,
                _SESSION_MAX_MESSAGES,
            )
            logger.info(
                "session window trimmed session_key=%s total=%d kept=%d",
                session_key,
                total_messages,
                len(prior_messages),
            )
        if not session_id:
            session_id = uuid4().hex[:12]

        engine = QueryEngine(
            api_client=self._client,
            tool_registry=oh_registry,
            permission_checker=PermissionChecker(PermissionSettings(mode=PermissionMode.FULL_AUTO)),
            cwd=Path.cwd(),
            model=self._settings.model,
            system_prompt=system_prompt_override or _build_system_prompt(workspace),
            max_tokens=self._settings.max_tokens,
            max_turns=_MAX_TURNS,
            settings=self._settings,
            tool_metadata={
                "session_id": session_id,
                "extra_skill_dirs": skill_dirs,
                "user_skills_dir": str(get_skills_dir(workspace)),
                "skill_registry_cwd": None,
                ToolMetadataKey.VISION_MODEL_CONFIG.value: _resolve_vision_config(self._settings),
                ToolMetadataKey.VISION_CALL_RECORDER.value: self._store.record_vision_call,
                "autodream_context": _autodream_context(workspace),
            },
        )
        engine.tool_metadata["system_prompt_refresher"] = lambda: engine.set_system_prompt(
            system_prompt_override or _build_system_prompt(workspace)
        )
        if prior_messages:
            engine.load_messages(sanitize_conversation_messages(prior_messages))

        # Prefix the user message with volatile context so the *system prompt*
        # remains static and can be fully KV-Cache shared across turns.
        search_text, prepared_user_text = _prepare_user_turn_text(user_text, source_context)
        prefix = _FACT_DISCIPLINE_CONTEXT
        if include_time_context:
            prefix += build_time_context()
        if include_similar_context:
            prefix += _build_similar_records_context(self._store, search_text)
        user_message = _build_user_message(prefix + prepared_user_text, media)

        yield ("progress", "🤔 正在思考...")
        last_text = ""
        tool_outputs: list[tuple[str, str]] = []
        tool_errors: list[str] = []
        passthrough_output: str = ""
        engine_error: str = ""
        stream_finished_reason: str | None = None
        emitted_media: set[str] = set()
        had_reasoning = False
        hint_dedupe_enabled = os.environ.get(_WOLO_DISABLE_HINT_DEDUPE) != "1"
        emitted_tool_hints: set[tuple[str, str]] = set()
        try:
            async for event in engine.submit_message(user_message):
                if isinstance(event, ReasoningDelta):
                    had_reasoning = True
                    yield ("reasoning", event.text)
                elif isinstance(event, AssistantTextDelta):
                    yield ("delta", event.text)
                elif isinstance(event, ToolExecutionStarted):
                    signature = (
                        event.tool_name,
                        json.dumps(event.tool_input or {}, ensure_ascii=False, sort_keys=True, default=str),
                    )
                    if not hint_dedupe_enabled or signature not in emitted_tool_hints:
                        yield ("tool_hint", _format_tool_hint(event.tool_name, event.tool_input))
                    emitted_tool_hints.add(signature)
                elif isinstance(event, AssistantTurnComplete):
                    candidate = event.message.text.strip()
                    if candidate and not event.message.tool_uses:
                        last_text = candidate
                elif isinstance(event, ToolExecutionCompleted):
                    if not event.is_error and "\u274c" in event.output:
                        logger.debug(
                            "wolo tool blocked or failed tool=%s output=%r",
                            event.tool_name, event.output.strip()[:200],
                        )
                    if event.is_error:
                        tool_errors.append(f"{event.tool_name}: {event.output.strip()[:200]}")
                    elif event.output.strip():
                        output = event.output.strip()
                        tool_outputs.append((event.tool_name, output))
                        if event.tool_name in PASSTHROUGH_TOOLS:
                            passthrough_output = output
                    tool_media = _extract_tool_media(event)
                    for path in tool_media:
                        emitted_media.add(path)
                    if tool_media:
                        yield ("media", json.dumps(tool_media))
                elif isinstance(event, StreamFinished):
                    stream_finished_reason = event.reason
        except Exception as exc:
            engine_error = f"{type(exc).__name__}: {exc}"
            logger.exception("WoloQueryRunner engine error session_key=%r text=%r", session_key, user_text[:80])

        # One-shot retry when the thinking model exhausted tokens without visible output
        if (
            not last_text
            and not tool_outputs
            and had_reasoning
            and not engine_error
            and stream_finished_reason is None
        ):
            yield ("progress", "⏳ 模型思考完毕但未产生输出，正在重试...")
            try:
                async for event in engine.submit_message("请直接输出记录结果，不需要过多思考。"):
                    if isinstance(event, AssistantTextDelta):
                        yield ("delta", event.text)
                    elif isinstance(event, AssistantTurnComplete):
                        candidate = event.message.text.strip()
                        if candidate and not event.message.tool_uses:
                            last_text = candidate
                    elif isinstance(event, ToolExecutionStarted):
                        signature = (
                            event.tool_name,
                            json.dumps(event.tool_input or {}, ensure_ascii=False, sort_keys=True, default=str),
                        )
                        if not hint_dedupe_enabled or signature not in emitted_tool_hints:
                            yield ("tool_hint", _format_tool_hint(event.tool_name, event.tool_input))
                        emitted_tool_hints.add(signature)
                    elif isinstance(event, ToolExecutionCompleted):
                        if not event.is_error and "\u274c" in event.output:
                            logger.debug(
                                "wolo retry: tool blocked tool=%s output=%r",
                                event.tool_name, event.output.strip()[:200],
                            )
                        if event.is_error:
                            tool_errors.append(f"{event.tool_name}: {event.output.strip()[:200]}")
                        elif event.output.strip():
                            output = event.output.strip()
                            tool_outputs.append((event.tool_name, output))
                            if event.tool_name in PASSTHROUGH_TOOLS:
                                passthrough_output = output
                        tool_media = _extract_tool_media(event)
                        for path in tool_media:
                            emitted_media.add(path)
                        if tool_media:
                            yield ("media", json.dumps(tool_media))
                    elif isinstance(event, StreamFinished):
                        stream_finished_reason = event.reason
            except Exception as exc:
                engine_error = f"{type(exc).__name__}: {exc}"
                logger.exception("WoloQueryRunner retry error session_key=%r text=%r", session_key, user_text[:80])

        if session_key and persist_session:
            save_conversation(workspace, session_key, engine.messages, session_id=session_id)

        if passthrough_output and stream_finished_reason is None:
            final = passthrough_output
        elif last_text:
            final = last_text
        elif tool_outputs and stream_finished_reason is None:
            final = "\n".join(output for _, output in tool_outputs)
        elif tool_outputs:
            final = next(
                (
                    output
                    for tool_name, output in tool_outputs
                    if tool_name.startswith("wolo_")
                ),
                "抱歉，这轮处理在工具调用过程中被中断，请稍后重试。",
            )
            logger.warning(
                "wolo abnormal termination — stream_finished_reason=%s tool_outputs=%s session_key=%s text_preview=%r",
                stream_finished_reason,
                [name for name, _ in tool_outputs],
                session_key,
                user_text[:120],
            )
        elif had_reasoning:
            final = "抱歉，模型思考后未能产生有效输出，请稍后重试。"
            logger.warning(
                "wolo empty-response fallback — had_reasoning=True engine_error=%s session_key=%s text_preview=%r",
                engine_error, session_key, user_text[:120],
            )
        else:
            final = FALLBACK_MESSAGE
            logger.warning(
                "wolo fallback triggered — last_text=%r tool_outputs=%s "
                "tool_errors=%s engine_error=%s session_key=%s text_preview=%r",
                last_text,
                [output[:80] for _, output in tool_outputs],
                tool_errors,
                engine_error,
                session_key,
                user_text[:120],
            )
        remaining_media = _extract_final_reply_media(final, emitted_media)
        for path in remaining_media:
            yield ("media", json.dumps([path]))
        yield ("final", final)

    async def run(
        self,
        user_text: str,
        session_key: str = "",
        *,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
        allow_tools: bool = True,
        include_time_context: bool = True,
        include_similar_context: bool = True,
        use_session_history: bool = True,
        persist_session: bool = True,
        system_prompt_override: str | None = None,
    ) -> str:
        async for kind, text in self.stream_run(
            user_text,
            session_key,
            media=media,
            source_context=source_context,
            allow_tools=allow_tools,
            include_time_context=include_time_context,
            include_similar_context=include_similar_context,
            use_session_history=use_session_history,
            persist_session=persist_session,
            system_prompt_override=system_prompt_override,
        ):
            if kind == "final":
                return text
        return FALLBACK_MESSAGE
