"""Per-session conversation history persistence for wolo (SQLite-backed)."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from common.conversation_history import stabilize_conversation_history
from openharness.engine.messages import ConversationMessage, sanitize_conversation_messages
from openharness.utils.fs import atomic_write_text

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


def _write_dream_snapshot(
    workspace: Path,
    session_key: str,
    session_id: str,
    messages: list[ConversationMessage],
) -> None:
    """Write session-{id}.json to the sessions dir so autodream can scan it."""
    sessions_dir = get_sessions_dir(workspace)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    summary = next((m.text.strip()[:80] for m in messages if m.role == "user" and m.text.strip()), "")
    payload = {
        "session_id": session_id,
        "session_key": session_key,
        "messages": [m.model_dump(mode="json") for m in messages],
        "message_count": len(messages),
        "created_at": time.time(),
        "summary": summary,
    }
    path = sessions_dir / f"session-{session_id}.json"
    try:
        atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        logger.warning("wolo session: failed to write dream snapshot %s", path)


def _migrate_legacy_sessions_to_sqlite(workspace: Path, conn: sqlite3.Connection) -> None:
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
    clean, changed = stabilize_conversation_history(sanitize_conversation_messages(messages))
    sid = session_id or uuid4().hex[:12]
    messages_json = json.dumps([m.model_dump(mode="json") for m in clean], ensure_ascii=False)
    now = _now_iso()

    conn = _get_db(workspace)
    try:
        _migrate_legacy_sessions_to_sqlite(workspace, conn)
        conn.execute(
            "INSERT INTO conversations (session_key, session_id, messages, message_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_key) DO UPDATE SET "
            "session_id=excluded.session_id, messages=excluded.messages, "
            "message_count=excluded.message_count, updated_at=excluded.updated_at",
            (session_key, sid, messages_json, len(clean), now, now),
        )
        conn.commit()
        if changed:
            logger.info(
                "wolo session: dropped incomplete trailing turn session_key=%s kept=%d",
                session_key,
                len(clean),
            )
        logger.debug("wolo session saved session_key=%s messages=%d", session_key, len(clean))
    finally:
        conn.close()
    _write_dream_snapshot(workspace, session_key, sid, clean)


def load_conversation(
    workspace: Path,
    session_key: str,
) -> tuple[list[ConversationMessage], str | None]:
    """Load conversation history for a session_key.

    Returns (messages, session_id).  Both are empty / None if no snapshot exists.
    """
    conn = _get_db(workspace)
    healed_messages: list[ConversationMessage] | None = None
    healed_session_id: str | None = None
    result: tuple[list[ConversationMessage], str | None] = ([], None)
    try:
        _migrate_legacy_sessions_to_sqlite(workspace, conn)
        cur = conn.execute(
            "SELECT session_id, messages FROM conversations WHERE session_key = ?",
            (session_key,),
        )
        row = cur.fetchone()
        if row is None:
            return result
        session_id, messages_json = row
        raw = json.loads(messages_json)
        messages, changed = stabilize_conversation_history(
            sanitize_conversation_messages(
                [ConversationMessage.model_validate(m) for m in raw]
            )
        )
        if changed:
            healed_messages = list(messages)
            healed_session_id = session_id
            healed_json = json.dumps([m.model_dump(mode="json") for m in messages], ensure_ascii=False)
            conn.execute(
                "UPDATE conversations SET messages = ?, message_count = ?, updated_at = ? WHERE session_key = ?",
                (healed_json, len(messages), _now_iso(), session_key),
            )
            conn.commit()
            logger.info(
                "wolo session: healed incomplete trailing turn session_key=%s kept=%d",
                session_key,
                len(messages),
            )
        logger.debug(
            "wolo session loaded session_key=%s messages=%d session_id=%s",
            session_key,
            len(messages),
            session_id,
        )
        result = (messages, session_id)
    except Exception:
        logger.warning("wolo session load failed for session_key=%s, starting fresh", session_key, exc_info=True)
        result = ([], None)
    finally:
        conn.close()
    if healed_messages is not None and healed_session_id is not None:
        _write_dream_snapshot(workspace, session_key, healed_session_id, healed_messages)
    return result


def list_conversations(workspace: Path, limit: int = 20) -> list[dict]:
    """List recent conversations, newest first."""
    conn = _get_db(workspace)
    try:
        _migrate_legacy_sessions_to_sqlite(workspace, conn)
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
