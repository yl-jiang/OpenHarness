"""Per-session conversation history persistence for wolo (SQLite-backed)."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages

from wolo.core.workspace import get_data_dir, get_sessions_dir

logger = logging.getLogger(__name__)

_CONVERSATIONS_SCHEMA = """\
CREATE TABLE IF NOT EXISTS conversations (
    session_key TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    messages TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_conversations_session_id ON conversations(session_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_db(workspace: Path) -> sqlite3.Connection:
    """Open a connection to the workspace store.db and ensure conversations table exists."""
    db_path = get_data_dir(workspace) / "store.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_CONVERSATIONS_SCHEMA)
    return conn


def _maybe_migrate_json_sessions(workspace: Path, conn: sqlite3.Connection) -> None:
    """One-time migration from JSON session files into SQLite."""
    cur = conn.execute(
        "SELECT 1 FROM conversations LIMIT 1"
    )
    if cur.fetchone() is not None:
        return  # already has data, skip migration

    sessions_dir = get_sessions_dir(workspace)
    if not sessions_dir.exists():
        return

    migrated = 0
    for path in sessions_dir.glob("latest-*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            session_key = data.get("session_key", "")
            session_id = data.get("session_id", "")
            messages_json = json.dumps(data.get("messages", []), ensure_ascii=False)
            message_count = data.get("message_count", 0)
            if not session_key:
                continue
            now = _now_iso()
            conn.execute(
                "INSERT OR IGNORE INTO conversations (session_key, session_id, messages, message_count, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session_key, session_id, messages_json, message_count, now, now),
            )
            migrated += 1
        except (json.JSONDecodeError, OSError):
            continue

    if migrated:
        conn.commit()
        logger.info("wolo session: migrated %d JSON sessions into SQLite", migrated)


def save_conversation(
    workspace: Path,
    session_key: str,
    messages: list[ConversationMessage],
    session_id: str | None = None,
) -> None:
    """Persist the latest conversation history for a session_key."""
    clean = sanitize_conversation_messages(messages)
    sid = session_id or uuid4().hex[:12]
    messages_json = json.dumps([m.model_dump(mode="json") for m in clean], ensure_ascii=False)
    now = _now_iso()

    conn = _get_db(workspace)
    try:
        _maybe_migrate_json_sessions(workspace, conn)
        conn.execute(
            "INSERT INTO conversations (session_key, session_id, messages, message_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_key) DO UPDATE SET "
            "session_id=excluded.session_id, messages=excluded.messages, "
            "message_count=excluded.message_count, updated_at=excluded.updated_at",
            (session_key, sid, messages_json, len(clean), now, now),
        )
        conn.commit()
        logger.debug("wolo session saved session_key=%s messages=%d", session_key, len(clean))
    finally:
        conn.close()


def load_conversation(
    workspace: Path,
    session_key: str,
) -> tuple[list[ConversationMessage], str | None]:
    """Load conversation history for a session_key.

    Returns (messages, session_id).  Both are empty / None if no snapshot exists.
    """
    conn = _get_db(workspace)
    try:
        _maybe_migrate_json_sessions(workspace, conn)
        cur = conn.execute(
            "SELECT session_id, messages FROM conversations WHERE session_key = ?",
            (session_key,),
        )
        row = cur.fetchone()
        if row is None:
            return [], None
        session_id, messages_json = row
        raw = json.loads(messages_json)
        messages = sanitize_conversation_messages(
            [ConversationMessage.model_validate(m) for m in raw]
        )
        logger.debug(
            "wolo session loaded session_key=%s messages=%d session_id=%s",
            session_key,
            len(messages),
            session_id,
        )
        return messages, session_id
    except Exception:
        logger.warning("wolo session load failed for session_key=%s, starting fresh", session_key, exc_info=True)
        return [], None
    finally:
        conn.close()


def list_conversations(workspace: Path, limit: int = 20) -> list[dict]:
    """List recent conversations, newest first."""
    conn = _get_db(workspace)
    try:
        _maybe_migrate_json_sessions(workspace, conn)
        cur = conn.execute(
            "SELECT session_key, session_id, message_count, updated_at "
            "FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        )
        return [
            {"session_key": r[0], "session_id": r[1], "message_count": r[2], "updated_at": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()
