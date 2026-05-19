"""Per-session conversation history persistence for solo."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.utils.fs import atomic_write_text

from solo.workspace import get_sessions_dir

logger = logging.getLogger(__name__)


def _sessions_dir(workspace: Path) -> Path:
    d = get_sessions_dir(workspace)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _snapshot_path(workspace: Path, session_key: str) -> Path:
    token = hashlib.sha1(session_key.encode("utf-8")).hexdigest()[:12]
    return _sessions_dir(workspace) / f"latest-{token}.json"


def _session_path(workspace: Path, session_id: str) -> Path:
    return _sessions_dir(workspace) / f"session-{session_id}.json"


def save_conversation(
    workspace: Path,
    session_key: str,
    messages: list[ConversationMessage],
    session_id: str | None = None,
) -> None:
    """Persist the latest conversation history for a session_key."""
    clean = sanitize_conversation_messages(messages)
    payload = {
        "session_key": session_key,
        "session_id": session_id or uuid4().hex[:12],
        "messages": [m.model_dump(mode="json") for m in clean],
        "message_count": len(clean),
    }
    path = _snapshot_path(workspace, session_key)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, data)
    atomic_write_text(_session_path(workspace, payload["session_id"]), data)
    logger.debug("solo session saved session_key=%s messages=%d path=%s", session_key, len(clean), path)


def load_conversation(
    workspace: Path,
    session_key: str,
) -> tuple[list[ConversationMessage], str | None]:
    """Load conversation history for a session_key.

    Returns (messages, session_id).  Both are empty / None if no snapshot exists.
    """
    path = _snapshot_path(workspace, session_key)
    if not path.exists():
        return [], None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw = data.get("messages") or []
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in raw]
        )
        session_id: str | None = data.get("session_id") or None
        logger.debug(
            "solo session loaded session_key=%s messages=%d session_id=%s",
            session_key,
            len(messages),
            session_id,
        )
        return messages, session_id
    except Exception:
        logger.warning("solo session load failed for session_key=%s, starting fresh", session_key, exc_info=True)
        return [], None
