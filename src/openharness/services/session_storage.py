"""Session persistence helpers."""

from __future__ import annotations

import json
import time
from datetime import datetime
from html import escape
from hashlib import sha1
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.api.usage import UsageSnapshot
from openharness.config.paths import get_sessions_dir
from openharness.engine.messages import (
    ConversationMessage,
    ImageBlock,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    sanitize_conversation_messages,
)
from openharness.engine.types import ToolMetadataKey
from openharness.utils.fs import atomic_write_text


_PERSISTED_TOOL_METADATA_KEYS = tuple(key.value for key in ToolMetadataKey.all_persisted_keys())


def _sanitize_metadata(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _sanitize_metadata(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_sanitize_metadata(item) for item in value]
    return str(value)


def _persistable_tool_metadata(tool_metadata: dict[str, object] | None) -> dict[str, Any]:
    if not isinstance(tool_metadata, dict):
        return {}
    payload: dict[str, Any] = {}
    for key in _PERSISTED_TOOL_METADATA_KEYS:
        if key in tool_metadata:
            payload[key] = _sanitize_metadata(tool_metadata[key])
    return payload


def get_project_session_dir(cwd: str | Path) -> Path:
    """Return the session directory for a project."""
    path = Path(cwd).resolve()
    digest = sha1(str(path).encode("utf-8")).hexdigest()[:12]
    session_dir = get_sessions_dir() / f"{path.name}-{digest}"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def save_session_snapshot(
    *,
    cwd: str | Path,
    model: str,
    system_prompt: str,
    messages: list[ConversationMessage],
    usage: UsageSnapshot,
    session_id: str | None = None,
    tool_metadata: dict[str, object] | None = None,
) -> Path:
    """Persist a session snapshot. Saves both by ID and as latest."""
    session_dir = get_project_session_dir(cwd)
    sid = session_id or uuid4().hex[:12]
    now = time.time()
    messages = sanitize_conversation_messages(messages)
    # Extract a summary from the first user message
    summary = ""
    for msg in messages:
        if msg.role == "user" and msg.text.strip():
            summary = msg.text.strip()[:80]
            break

    payload = {
        "session_id": sid,
        "cwd": str(Path(cwd).resolve()),
        "model": model,
        "system_prompt": system_prompt,
        "messages": [message.model_dump(mode="json") for message in messages],
        "usage": usage.model_dump(),
        "tool_metadata": _persistable_tool_metadata(tool_metadata),
        "created_at": now,
        "summary": summary,
        "message_count": len(messages),
    }
    data = json.dumps(payload, indent=2) + "\n"

    # Save as latest
    latest_path = session_dir / "latest.json"
    atomic_write_text(latest_path, data)

    # Save by session ID
    session_path = session_dir / f"session-{sid}.json"
    atomic_write_text(session_path, data)

    return latest_path


def _sanitize_snapshot_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize persisted messages for forward compatibility."""
    raw_messages = payload.get("messages", [])
    if isinstance(raw_messages, list):
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(item) for item in raw_messages]
        )
        payload = dict(payload)
        payload["messages"] = [message.model_dump(mode="json") for message in messages]
        payload["message_count"] = len(messages)
    return payload


def load_session_snapshot(cwd: str | Path) -> dict[str, Any] | None:
    """Load the most recent session snapshot for the project."""
    path = get_project_session_dir(cwd) / "latest.json"
    if not path.exists():
        return None
    return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))


def list_session_snapshots(cwd: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    """List saved sessions for the project, newest first."""
    session_dir = get_project_session_dir(cwd)
    sessions: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    # Named session files
    for path in sorted(session_dir.glob("session-*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            sid = data.get("session_id", path.stem.replace("session-", ""))
            seen_ids.add(sid)
            summary = data.get("summary", "")
            if not summary:
                # Extract from first user message
                for msg in data.get("messages", []):
                    if msg.get("role") == "user":
                        texts = [b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"]
                        summary = " ".join(texts).strip()[:80]
                        if summary:
                            break
            sessions.append({
                "session_id": sid,
                "summary": summary,
                "message_count": data.get("message_count", len(data.get("messages", []))),
                "model": data.get("model", ""),
                "created_at": data.get("created_at", path.stat().st_mtime),
            })
        except (json.JSONDecodeError, OSError):
            continue
        if len(sessions) >= limit:
            break

    # Also include latest.json if it has no corresponding session file
    latest_path = session_dir / "latest.json"
    if latest_path.exists() and len(sessions) < limit:
        try:
            data = json.loads(latest_path.read_text(encoding="utf-8"))
            sid = data.get("session_id", "latest")
            if sid not in seen_ids:
                summary = data.get("summary", "")
                if not summary:
                    for msg in data.get("messages", []):
                        if msg.get("role") == "user":
                            texts = [b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text"]
                            summary = " ".join(texts).strip()[:80]
                            if summary:
                                break
                sessions.append({
                    "session_id": sid,
                    "summary": summary or "(latest session)",
                    "message_count": data.get("message_count", len(data.get("messages", []))),
                    "model": data.get("model", ""),
                    "created_at": data.get("created_at", latest_path.stat().st_mtime),
                })
        except (json.JSONDecodeError, OSError):
            pass

    # Sort by created_at descending
    sessions.sort(key=lambda s: s.get("created_at", 0), reverse=True)
    return sessions[:limit]


def load_session_by_id(cwd: str | Path, session_id: str) -> dict[str, Any] | None:
    """Load a specific session by ID."""
    session_dir = get_project_session_dir(cwd)
    # Try named session first
    path = session_dir / f"session-{session_id}.json"
    if path.exists():
        return _sanitize_snapshot_payload(json.loads(path.read_text(encoding="utf-8")))
    # Fallback to latest.json if session_id matches
    latest = session_dir / "latest.json"
    if latest.exists():
        data = _sanitize_snapshot_payload(json.loads(latest.read_text(encoding="utf-8")))
        if data.get("session_id") == session_id or session_id == "latest":
            return data
    return None


def _safe_filename_part(value: str) -> str:
    safe = "".join(character.lower() for character in value if character.isalnum() or character in {"-", "_"})
    return safe.strip("-_") or "session"


def _clip_line(value: str, limit: int = 120) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3].rstrip() + "..."


def _first_user_topic(messages: list[ConversationMessage]) -> str:
    for message in messages:
        if message.role == "user" and not _is_tool_result_only(message) and message.text.strip():
            return _clip_line(message.text)
    return "(no user message)"


def _is_tool_result_only(message: ConversationMessage) -> bool:
    return (
        message.role == "user"
        and bool(message.content)
        and all(isinstance(block, ToolResultBlock) for block in message.content)
    )


def _turns(messages: list[ConversationMessage]) -> list[list[ConversationMessage]]:
    turns: list[list[ConversationMessage]] = []
    current: list[ConversationMessage] | None = None
    for message in messages:
        if message.role == "user" and not _is_tool_result_only(message):
            current = [message]
            turns.append(current)
            continue
        if current is None:
            current = [message]
            turns.append(current)
        else:
            current.append(message)
    return turns


def _render_message_body(message: ConversationMessage) -> str:
    chunks: list[str] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            text = block.text.strip()
            if text:
                chunks.append(text)
        elif isinstance(block, ImageBlock):
            if block.source_path:
                chunks.append(f"![Image]({block.source_path})")
            else:
                chunks.append(f"_[Image: {block.media_type}]_")
    return "\n\n".join(chunks)


def _append_tool_call(parts: list[str], block: ToolUseBlock) -> None:
    parts.extend(
        [
            f"#### Tool Call: {block.name}",
            f"<!-- call_id: {block.id} -->",
            "```json",
            json.dumps(block.input, indent=2, ensure_ascii=False),
            "```",
            "",
        ]
    )


def _append_tool_result(parts: list[str], block: ToolResultBlock, tool_name: str) -> None:
    label = f"Tool Result: {tool_name}"
    if block.is_error:
        label = f"Tool Result (error): {tool_name}"
    parts.extend(
        [
            f"<details><summary>{escape(label)}</summary>",
            "",
            f"<!-- call_id: {block.tool_use_id} -->",
            "",
            block.content.strip(),
            "",
            "</details>",
            "",
        ]
    )


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Unable to allocate an export path for {path}")


def _render_session_export_markdown(
    *,
    cwd: str | Path,
    messages: list[ConversationMessage],
    usage: UsageSnapshot | None,
    session_id: str,
    exported_at: datetime,
    app_name: str,
) -> str:
    token_count = usage.total_tokens if usage is not None else 0
    tool_count = sum(len(message.tool_uses) for message in messages)
    grouped_turns = _turns(messages)
    title = f"{app_name} Session Export"
    parts = [
        "---",
        f"session_id: {session_id}",
        f"exported_at: {exported_at.isoformat(timespec='seconds')}",
        f"work_dir: {Path(cwd).resolve()}",
        f"message_count: {len(messages)}",
        f"token_count: {token_count}",
        "---",
        "",
        f"# {title}",
        "",
        "## Overview",
        "",
        f"- **Topic**: {_first_user_topic(messages)}",
        f"- **Conversation**: {len(grouped_turns)} turns | {tool_count} tool calls | {token_count:,} tokens",
        "",
        "---",
        "",
    ]

    tool_names_by_id: dict[str, str] = {}
    for turn_index, turn in enumerate(grouped_turns, start=1):
        parts.extend([f"## Turn {turn_index}", ""])
        for message in turn:
            if message.role == "user" and not _is_tool_result_only(message):
                parts.extend(["### User", ""])
                body = _render_message_body(message)
                if body:
                    parts.extend([body, ""])
                continue
            if message.role == "assistant":
                parts.extend(["### Assistant", ""])
                body = _render_message_body(message)
                if body:
                    parts.extend([body, ""])
                for block in message.tool_uses:
                    tool_names_by_id[block.id] = block.name
                    _append_tool_call(parts, block)
                continue
            for block in message.content:
                if isinstance(block, ToolResultBlock):
                    _append_tool_result(parts, block, tool_names_by_id.get(block.tool_use_id, "unknown"))
    return "\n".join(parts).strip() + "\n"


def export_session_markdown(
    *,
    cwd: str | Path,
    messages: list[ConversationMessage],
    usage: UsageSnapshot | None = None,
    session_id: str | None = None,
    output_dir: str | Path | None = None,
    app_name: str = "OpenHarness",
) -> Path:
    """Export the session transcript as Markdown."""
    resolved_session_id = session_id or uuid4().hex[:12]
    exported_at = datetime.now().astimezone()
    target_dir = Path(output_dir if output_dir is not None else cwd).expanduser().resolve()
    filename = (
        f"{_safe_filename_part(app_name)}-export-"
        f"{_safe_filename_part(resolved_session_id)[:8]}-"
        f"{exported_at.strftime('%Y%m%d-%H%M%S')}.md"
    )
    path = _unique_path(target_dir / filename)
    atomic_write_text(
        path,
        _render_session_export_markdown(
            cwd=cwd,
            messages=messages,
            usage=usage,
            session_id=resolved_session_id,
            exported_at=exported_at,
            app_name=app_name,
        ),
    )
    return path
