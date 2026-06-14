"""Onboard-specific chat message persistence (SQLite).

Stores clean display messages with timestamps, completely independent from the
runner's session storage (which holds LLM context including injected headers).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _get_db_path() -> Path:
    root = Path(os.environ.get("ONBOARD_WORKSPACE", "~/.onboard")).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root / "chat.db"


_SCHEMA = """\
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_key TEXT PRIMARY KEY,
    app_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_updated ON chat_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    FOREIGN KEY (session_key) REFERENCES chat_sessions(session_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_key, id);
"""

_initialized = False


def _get_conn() -> sqlite3.Connection:
    global _initialized
    conn = sqlite3.connect(str(_get_db_path()), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    if not _initialized:
        conn.executescript(_SCHEMA)
        _initialized = True
    return conn


def save_user_message(session_key: str, app_name: str, content: str) -> str:
    ts = _now_iso()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO chat_sessions (session_key, app_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(session_key) DO UPDATE SET updated_at=excluded.updated_at",
            (session_key, app_name, ts, ts),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_key, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_key, "user", content, ts),
        )
        conn.commit()
        return ts
    finally:
        conn.close()


def save_assistant_message(session_key: str, content: str) -> str:
    ts = _now_iso()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE chat_sessions SET updated_at = ? WHERE session_key = ?",
            (ts, session_key),
        )
        conn.execute(
            "INSERT INTO chat_messages (session_key, role, content, timestamp) VALUES (?, ?, ?, ?)",
            (session_key, "assistant", content, ts),
        )
        conn.commit()
        return ts
    finally:
        conn.close()


def list_sessions(
    app_name: str,
    *,
    limit: int = 50,
    search: str | None = None,
) -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        if search:
            kw = f"%{search}%"
            cur = conn.execute(
                "SELECT DISTINCT s.session_key, s.created_at, s.updated_at, "
                "  (SELECT COUNT(*) FROM chat_messages WHERE session_key = s.session_key) AS msg_count, "
                "  (SELECT content FROM chat_messages WHERE session_key = s.session_key AND role = 'user' ORDER BY id LIMIT 1) AS preview "
                "FROM chat_sessions s "
                "WHERE s.app_name = ? "
                "  AND EXISTS ("
                "    SELECT 1 FROM chat_messages m "
                "    WHERE m.session_key = s.session_key AND m.content LIKE ?"
                "  ) "
                "ORDER BY s.updated_at DESC LIMIT ?",
                (app_name, kw, limit),
            )
        else:
            cur = conn.execute(
                "SELECT s.session_key, s.created_at, s.updated_at, "
                "  (SELECT COUNT(*) FROM chat_messages WHERE session_key = s.session_key) AS msg_count, "
                "  (SELECT content FROM chat_messages WHERE session_key = s.session_key AND role = 'user' ORDER BY id LIMIT 1) AS preview "
                "FROM chat_sessions s "
                "WHERE s.app_name = ? "
                "ORDER BY s.updated_at DESC LIMIT ?",
                (app_name, limit),
            )
        return [
            {
                "session_key": r[0],
                "session_id": None,
                "message_count": r[3],
                "updated_at": r[2],
                "preview": (r[4] or "")[:100],
            }
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def load_session_messages(session_key: str) -> list[dict[str, Any]]:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT role, content, timestamp FROM chat_messages WHERE session_key = ? ORDER BY id",
            (session_key,),
        )
        return [
            {"role": r[0], "content": r[1], "timestamp": r[2]}
            for r in cur.fetchall()
        ]
    finally:
        conn.close()


def delete_session(session_key: str) -> bool:
    conn = _get_conn()
    try:
        cur = conn.execute("DELETE FROM chat_sessions WHERE session_key = ?", (session_key,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_session_info(session_key: str) -> dict[str, Any] | None:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT session_key, created_at, updated_at FROM chat_sessions WHERE session_key = ?",
            (session_key,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"session_key": row[0], "created_at": row[1], "updated_at": row[2]}
    finally:
        conn.close()
