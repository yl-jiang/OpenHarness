"""SQLite-backed storage for the standalone solo app."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from solo.core.attachments import (
    StoredAttachment,
    persist_attachment_paths,
    resolve_stored_attachment_path,
)
from solo.core.models import (
    PendingConfirmation,
    ProfileUpdate,
    SoloConfig,
    SoloEntry,
    SoloExperiment,
    SoloRecord,
    SoloReport,
    SoloTodo,
)
from solo.core.workspace import get_attachments_dir, get_data_dir, initialize_workspace
from solo.core.utils import _now

DB_FILENAME = "store.db"
_SCHEMA_VERSION = 2

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'local',
    sender_id TEXT NOT NULL DEFAULT '',
    chat_id TEXT NOT NULL DEFAULT '',
    message_id TEXT,
    metadata TEXT NOT NULL DEFAULT '{}',
    attachments TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries(created_at);

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    date TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    corrected_content TEXT NOT NULL,
    summary TEXT NOT NULL,
    tags TEXT NOT NULL,
    emotion TEXT NOT NULL,
    weekday TEXT NOT NULL DEFAULT '',
    events TEXT NOT NULL DEFAULT '',
    period TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT '',
    is_weekend INTEGER NOT NULL DEFAULT 0,
    content_length INTEGER NOT NULL DEFAULT 0,
    emotion_reason TEXT NOT NULL DEFAULT '',
    related_people TEXT NOT NULL DEFAULT '',
    related_places TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '原始',
    created_at TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '[]',
    sample_type TEXT NOT NULL DEFAULT 'neutral',
    trigger_scene TEXT NOT NULL DEFAULT '',
    friction_signal TEXT NOT NULL DEFAULT '',
    awareness_timing TEXT NOT NULL DEFAULT '',
    break_point TEXT NOT NULL DEFAULT '',
    bridge_action TEXT NOT NULL DEFAULT '',
    environment_design TEXT NOT NULL DEFAULT '',
    next_experiment TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_date ON records(date);
CREATE INDEX IF NOT EXISTS idx_records_emotion ON records(emotion);
CREATE INDEX IF NOT EXISTS idx_records_entry_id ON records(entry_id);

CREATE TABLE IF NOT EXISTS pending_confirmations (
    id TEXT PRIMARY KEY,
    entry_id TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    clarification_reason TEXT NOT NULL DEFAULT '',
    questions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS profile_updates (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    category TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_name TEXT NOT NULL,
    suggested_value TEXT NOT NULL,
    confidence TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
);

CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    report_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    title TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT 'medium',
    due_date TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'derived',
    created_at TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status);

CREATE TABLE IF NOT EXISTS experiments (
    id TEXT PRIMARY KEY,
    record_id TEXT NOT NULL,
    title TEXT NOT NULL,
    hypothesis TEXT NOT NULL DEFAULT '',
    trigger TEXT NOT NULL DEFAULT '',
    desired_action TEXT NOT NULL DEFAULT '',
    environment_design TEXT NOT NULL DEFAULT '',
    success_criteria TEXT NOT NULL DEFAULT '',
    observation_window TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT 'derived',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status);

CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class SoloStore:
    """SQLite-backed solo store rooted in the solo workspace."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = initialize_workspace(workspace)
        self.root = get_data_dir(self.workspace)
        self.attachments_root = get_attachments_dir(self.workspace)
        self._db_path = self.root / DB_FILENAME
        self._conn: sqlite3.Connection | None = None

    @property
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._ensure_db()
        return self._conn  # type: ignore[return-value]

    def _ensure_db(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=10)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._apply_migrations()
        cur = self._conn.execute("SELECT value FROM _meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif row[0] != str(_SCHEMA_VERSION):
            self._conn.execute(
                "UPDATE _meta SET value=? WHERE key='schema_version'",
                (str(_SCHEMA_VERSION),),
            )
        self._conn.commit()
        self._maybe_migrate_jsonl()

    def _apply_migrations(self) -> None:
        assert self._conn is not None
        record_columns = {
            "sample_type": "TEXT NOT NULL DEFAULT 'neutral'",
            "trigger_scene": "TEXT NOT NULL DEFAULT ''",
            "friction_signal": "TEXT NOT NULL DEFAULT ''",
            "awareness_timing": "TEXT NOT NULL DEFAULT ''",
            "break_point": "TEXT NOT NULL DEFAULT ''",
            "bridge_action": "TEXT NOT NULL DEFAULT ''",
            "environment_design": "TEXT NOT NULL DEFAULT ''",
            "next_experiment": "TEXT NOT NULL DEFAULT ''",
        }
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(records)").fetchall()
        }
        for name, definition in record_columns.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE records ADD COLUMN {name} {definition}")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS experiments (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL,
                title TEXT NOT NULL,
                hypothesis TEXT NOT NULL DEFAULT '',
                trigger TEXT NOT NULL DEFAULT '',
                desired_action TEXT NOT NULL DEFAULT '',
                environment_design TEXT NOT NULL DEFAULT '',
                success_criteria TEXT NOT NULL DEFAULT '',
                observation_window TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                source TEXT NOT NULL DEFAULT 'derived',
                created_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_experiments_status ON experiments(status)")

    def _maybe_migrate_jsonl(self) -> None:
        """Import existing JSONL data into SQLite on first run."""
        cur = self._db.execute("SELECT value FROM _meta WHERE key='migrated_jsonl'")
        if cur.fetchone() is not None:
            return
        migrated_any = False
        migrated_any |= self._migrate_entries_jsonl()
        migrated_any |= self._migrate_records_jsonl()
        migrated_any |= self._migrate_simple_jsonl(
            "pending_confirmations", "pending_confirmations.jsonl",
            PendingConfirmation.from_json, self._pending_confirmation_to_row,
        )
        migrated_any |= self._migrate_simple_jsonl(
            "profile_updates", "profile_updates.jsonl",
            ProfileUpdate.from_json, self._profile_update_to_row,
        )
        migrated_any |= self._migrate_simple_jsonl(
            "reports", "reports.jsonl",
            SoloReport.from_json, self._report_to_row,
        )
        migrated_any |= self._migrate_simple_jsonl(
            "todos", "todos.jsonl",
            SoloTodo.from_json, self._todo_to_row,
        )
        migrated_any |= self._migrate_simple_jsonl(
            "experiments", "experiments.jsonl",
            SoloExperiment.from_json, self._experiment_to_row,
        )
        self._db.execute(
            "INSERT INTO _meta (key, value) VALUES ('migrated_jsonl', ?)",
            (_now(),),
        )
        self._db.commit()
        if migrated_any:
            for name in (
                "entries.jsonl", "records.jsonl", "pending_confirmations.jsonl",
                "profile_updates.jsonl", "reports.jsonl", "todos.jsonl", "experiments.jsonl",
            ):
                path = self.root / name
                if path.exists() and path.stat().st_size > 0:
                    path.rename(path.with_suffix(".jsonl.bak"))

    def _migrate_entries_jsonl(self) -> bool:
        path = self.root / "entries.jsonl"
        if not path.exists() or path.stat().st_size == 0:
            return False
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return False
        for line in lines:
            entry = SoloEntry.from_json(line)
            self._db.execute(
                "INSERT OR IGNORE INTO entries (id, content, created_at, channel, sender_id, chat_id, message_id, metadata, attachments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry.id, entry.content, entry.created_at, entry.channel,
                    entry.sender_id, entry.chat_id, entry.message_id,
                    json.dumps(entry.metadata or {}, ensure_ascii=False),
                    json.dumps([a.to_dict() for a in entry.attachments], ensure_ascii=False),
                ),
            )
        self._db.commit()
        return True

    def _migrate_records_jsonl(self) -> bool:
        path = self.root / "records.jsonl"
        if not path.exists() or path.stat().st_size == 0:
            return False
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return False
        for line in lines:
            record = SoloRecord.from_json(line)
            self._db.execute(
                "INSERT OR IGNORE INTO records "
                "(id, entry_id, date, raw_content, corrected_content, summary, tags, emotion, "
                "weekday, events, period, season, is_weekend, content_length, emotion_reason, "
                "related_people, related_places, source, created_at, attachments) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.id, record.entry_id, record.date, record.raw_content,
                    record.corrected_content, record.summary, record.tags, record.emotion,
                    record.weekday, record.events, record.period, record.season,
                    int(record.is_weekend), record.content_length, record.emotion_reason,
                    record.related_people, record.related_places, record.source,
                    record.created_at,
                    json.dumps([a.to_dict() for a in record.attachments], ensure_ascii=False),
                ),
            )
        self._db.commit()
        return True

    def _migrate_simple_jsonl(self, table: str, filename: str, from_json, to_row) -> bool:
        path = self.root / filename
        if not path.exists() or path.stat().st_size == 0:
            return False
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            return False
        for line in lines:
            obj = from_json(line)
            cols, vals = to_row(obj)
            placeholders = ", ".join("?" * len(vals))
            self._db.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        self._db.commit()
        return True

    # --- Row conversion helpers ---

    @staticmethod
    def _pending_confirmation_to_row(p: PendingConfirmation):
        cols = ("id", "entry_id", "raw_content", "clarification_reason", "questions", "created_at")
        vals = (p.id, p.entry_id, p.raw_content, p.clarification_reason, json.dumps(p.questions, ensure_ascii=False), p.created_at)
        return cols, vals

    @staticmethod
    def _profile_update_to_row(u: ProfileUpdate):
        cols = ("id", "record_id", "category", "entity_type", "entity_name", "suggested_value", "confidence", "status")
        vals = (u.id, u.record_id, u.category, u.entity_type, u.entity_name, u.suggested_value, u.confidence, u.status)
        return cols, vals

    @staticmethod
    def _report_to_row(r: SoloReport):
        cols = ("id", "report_type", "content", "created_at")
        vals = (r.id, r.report_type, r.content, r.created_at)
        return cols, vals

    @staticmethod
    def _todo_to_row(t: SoloTodo):
        cols = ("id", "record_id", "title", "category", "priority", "due_date", "status", "source", "created_at", "completed_at")
        vals = (t.id, t.record_id, t.title, t.category, t.priority, t.due_date, t.status, t.source, t.created_at, t.completed_at)
        return cols, vals

    @staticmethod
    def _experiment_to_row(e: SoloExperiment):
        cols = (
            "id", "record_id", "title", "hypothesis", "trigger", "desired_action",
            "environment_design", "success_criteria", "observation_window", "status",
            "source", "created_at",
        )
        vals = (
            e.id, e.record_id, e.title, e.hypothesis, e.trigger, e.desired_action,
            e.environment_design, e.success_criteria, e.observation_window, e.status,
            e.source, e.created_at,
        )
        return cols, vals

    # --- Public API (unchanged signatures) ---

    def initialize(self) -> Path:
        initialize_workspace(self.workspace)
        self.root.mkdir(parents=True, exist_ok=True)
        _ = self._db  # ensure DB created
        return self.root

    def load_config(self) -> SoloConfig:
        from solo.config import load_config

        return load_config(self.workspace)

    def save_config(self, config: SoloConfig) -> Path:
        from solo.config import save_config

        return save_config(config, self.workspace)

    def record(
        self,
        content: str,
        *,
        channel: str = "local",
        sender_id: str = "",
        chat_id: str = "",
        message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
        media: list[str] | None = None,
        source_context: dict[str, Any] | None = None,
    ) -> SoloEntry:
        text = content.strip()
        if not text:
            raise ValueError("solo content cannot be empty")
        self.initialize()
        context = dict(source_context or {})
        created_text = created_at or _now()
        entry_id = uuid4().hex[:12]
        entry_media = [str(item) for item in (context.get("media") or media or []) if str(item).strip()]
        attachments = self._persist_entry_attachments(entry_id, entry_media, created_text)
        entry = SoloEntry(
            id=entry_id,
            content=text,
            created_at=created_text,
            channel=str(context.get("channel") or channel),
            sender_id=str(context.get("sender_id") or sender_id),
            chat_id=str(context.get("chat_id") or chat_id),
            message_id=self._optional_text(context.get("message_id") or message_id),
            metadata=self._merge_entry_metadata(metadata, context),
            attachments=attachments,
        )
        self._db.execute(
            "INSERT INTO entries (id, content, created_at, channel, sender_id, chat_id, message_id, metadata, attachments) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry.id, entry.content, entry.created_at, entry.channel,
                entry.sender_id, entry.chat_id, entry.message_id,
                json.dumps(entry.metadata or {}, ensure_ascii=False),
                json.dumps([a.to_dict() for a in entry.attachments], ensure_ascii=False),
            ),
        )
        self._db.commit()
        return entry

    def list_entries(self, *, limit: int | None = None) -> list[SoloEntry]:
        if limit is not None:
            cur = self._db.execute(
                "SELECT * FROM entries ORDER BY rowid DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
            rows.reverse()
        else:
            cur = self._db.execute("SELECT * FROM entries ORDER BY rowid")
            rows = cur.fetchall()
        return [self._row_to_entry(row) for row in rows]

    def get_entry(self, entry_id: str) -> SoloEntry | None:
        cur = self._db.execute("SELECT * FROM entries WHERE id = ?", (entry_id,))
        row = cur.fetchone()
        return self._row_to_entry(row) if row else None

    def add_record(self, record: SoloRecord) -> None:
        self._db.execute(
            "INSERT INTO records "
            "(id, entry_id, date, raw_content, corrected_content, summary, tags, emotion, "
            "weekday, events, period, season, is_weekend, content_length, emotion_reason, "
            "related_people, related_places, source, created_at, attachments, sample_type, "
            "trigger_scene, friction_signal, awareness_timing, break_point, bridge_action, "
            "environment_design, next_experiment) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                record.id, record.entry_id, record.date, record.raw_content,
                record.corrected_content, record.summary, record.tags, record.emotion,
                record.weekday, record.events, record.period, record.season,
                int(record.is_weekend), record.content_length, record.emotion_reason,
                record.related_people, record.related_places, record.source,
                record.created_at,
                json.dumps([a.to_dict() for a in record.attachments], ensure_ascii=False),
                record.sample_type, record.trigger_scene, record.friction_signal,
                record.awareness_timing, record.break_point, record.bridge_action,
                record.environment_design, record.next_experiment,
            ),
        )
        self._db.commit()

    def list_records(self, *, limit: int | None = None) -> list[SoloRecord]:
        if limit is not None:
            cur = self._db.execute(
                "SELECT * FROM records ORDER BY rowid DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
            rows.reverse()
        else:
            cur = self._db.execute("SELECT * FROM records ORDER BY rowid")
            rows = cur.fetchall()
        return [self._row_to_record(row) for row in rows]

    def get_record(self, record_id: str) -> SoloRecord | None:
        cur = self._db.execute("SELECT * FROM records WHERE id = ?", (record_id,))
        row = cur.fetchone()
        return self._row_to_record(row) if row else None

    def resolve_attachment_path(self, attachment: StoredAttachment) -> Path:
        return resolve_stored_attachment_path(self.workspace, attachment)

    def update_record(self, record_id: str, **updates: Any) -> bool:
        """Update an existing record by ID with new field values."""
        record = self.get_record(record_id)
        if record is None:
            return False
        data = record.to_dict()
        data["attachments"] = list(record.attachments)
        data.update(updates)
        new_record = SoloRecord(**data)
        self._db.execute(
            "UPDATE records SET entry_id=?, date=?, raw_content=?, corrected_content=?, "
            "summary=?, tags=?, emotion=?, weekday=?, events=?, period=?, season=?, "
            "is_weekend=?, content_length=?, emotion_reason=?, related_people=?, "
            "related_places=?, source=?, created_at=?, attachments=?, sample_type=?, "
            "trigger_scene=?, friction_signal=?, awareness_timing=?, break_point=?, "
            "bridge_action=?, environment_design=?, next_experiment=? WHERE id=?",
            (
                new_record.entry_id, new_record.date, new_record.raw_content,
                new_record.corrected_content, new_record.summary, new_record.tags,
                new_record.emotion, new_record.weekday, new_record.events, new_record.period,
                new_record.season, int(new_record.is_weekend), new_record.content_length,
                new_record.emotion_reason, new_record.related_people, new_record.related_places,
                new_record.source, new_record.created_at,
                json.dumps([a.to_dict() for a in new_record.attachments], ensure_ascii=False),
                new_record.sample_type, new_record.trigger_scene, new_record.friction_signal,
                new_record.awareness_timing, new_record.break_point, new_record.bridge_action,
                new_record.environment_design, new_record.next_experiment,
                record_id,
            ),
        )
        self._db.commit()
        return True

    def delete_record(self, record_id: str) -> bool:
        """Permanently delete a record by ID."""
        cur = self._db.execute("DELETE FROM records WHERE id = ?", (record_id,))
        self._db.commit()
        return cur.rowcount > 0

    def add_pending_confirmation(self, pending: PendingConfirmation) -> None:
        cols, vals = self._pending_confirmation_to_row(pending)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO pending_confirmations ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def list_pending_confirmations(self) -> list[PendingConfirmation]:
        cur = self._db.execute("SELECT * FROM pending_confirmations ORDER BY rowid")
        return [self._row_to_pending_confirmation(row) for row in cur.fetchall()]

    def add_profile_update(self, update: ProfileUpdate) -> None:
        cols, vals = self._profile_update_to_row(update)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO profile_updates ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def list_profile_updates(self) -> list[ProfileUpdate]:
        cur = self._db.execute("SELECT * FROM profile_updates ORDER BY rowid")
        return [self._row_to_profile_update(row) for row in cur.fetchall()]

    def add_report(self, report: SoloReport) -> None:
        cols, vals = self._report_to_row(report)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO reports ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def list_reports(self) -> list[SoloReport]:
        cur = self._db.execute("SELECT * FROM reports ORDER BY rowid")
        return [self._row_to_report(row) for row in cur.fetchall()]

    def add_todo(self, todo: SoloTodo) -> None:
        cols, vals = self._todo_to_row(todo)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO todos ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def add_experiment(self, experiment: SoloExperiment) -> None:
        cols, vals = self._experiment_to_row(experiment)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO experiments ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def list_todos(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        limit: int | None = None,
    ) -> list[SoloTodo]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if category:
            clauses.append("LOWER(category) LIKE ?")
            params.append(f"%{category.lower()}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is not None:
            cur = self._db.execute(
                f"SELECT * FROM todos{where} ORDER BY rowid DESC LIMIT ?",
                params + [limit],
            )
            rows = cur.fetchall()
            rows.reverse()
        else:
            cur = self._db.execute(f"SELECT * FROM todos{where} ORDER BY rowid", params)
            rows = cur.fetchall()
        return [self._row_to_todo(row) for row in rows]

    def get_todo(self, todo_id: str) -> SoloTodo | None:
        """Fetch a single todo by ID, or None if not found."""
        cur = self._db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
        row = cur.fetchone()
        return self._row_to_todo(row) if row else None

    def list_experiments(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[SoloExperiment]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is not None:
            cur = self._db.execute(
                f"SELECT * FROM experiments{where} ORDER BY rowid DESC LIMIT ?",
                params + [limit],
            )
            rows = cur.fetchall()
            rows.reverse()
        else:
            cur = self._db.execute(f"SELECT * FROM experiments{where} ORDER BY rowid", params)
            rows = cur.fetchall()
        return [self._row_to_experiment(row) for row in rows]

    def complete_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='done', completed_at=? WHERE id=? AND status != 'done'",
            (_now(), todo_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def update_todo(self, todo_id: str, **updates: Any) -> bool:
        """Update an existing todo by ID with new field values."""
        cur = self._db.execute("SELECT * FROM todos WHERE id = ?", (todo_id,))
        row = cur.fetchone()
        if row is None:
            return False
        todo = self._row_to_todo(row)
        data = todo.to_dict()
        data.update(updates)
        if data.get("status") == "done" and not data.get("completed_at"):
            data["completed_at"] = _now()
        self._db.execute(
            "UPDATE todos SET record_id=?, title=?, category=?, priority=?, due_date=?, "
            "status=?, source=?, created_at=?, completed_at=? WHERE id=?",
            (
                data["record_id"], data["title"], data["category"], data["priority"],
                data["due_date"], data["status"], data["source"], data["created_at"],
                data["completed_at"], todo_id,
            ),
        )
        self._db.commit()
        return True

    def search_records(
        self,
        query: str | None = None,
        *,
        tags: list[str] | None = None,
        emotions: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
    ) -> list[SoloRecord]:
        """Search records with SQL filters and BM25 + Temporal Decay ranking."""
        clauses: list[str] = []
        params: list[Any] = []
        if start_date:
            clauses.append("date >= ?")
            params.append(start_date)
        if end_date:
            clauses.append("date <= ?")
            params.append(end_date)
        if emotions:
            placeholders = ", ".join("?" * len(emotions))
            clauses.append(f"emotion IN ({placeholders})")
            params.extend(emotions)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        cur = self._db.execute(f"SELECT * FROM records{where} ORDER BY rowid", params)
        filtered = [self._row_to_record(row) for row in cur.fetchall()]

        if tags:
            filtered = [
                r for r in filtered
                if any(t.strip().lower() in r.tags.lower() for t in tags)
            ]

        if not filtered:
            return []

        if not query:
            return filtered[-limit:]

        query_tokens = _tokenize_enhanced(query)
        if not query_tokens:
            return filtered[-limit:]

        from rank_bm25 import BM25Okapi

        corpus_tokens = [
            _tokenize_enhanced(
                f"{r.summary} {r.corrected_content} {r.tags} {r.weekday} {r.events} {r.period} {r.season} "
                f"{'周末' if r.is_weekend else '工作日'} {r.sample_type} {r.trigger_scene} "
                f"{r.break_point} {r.bridge_action} {r.environment_design} {r.next_experiment}"
            )
            for r in filtered
        ]

        bm25 = BM25Okapi(corpus_tokens)
        doc_scores = bm25.get_scores(query_tokens)

        scored: list[tuple[float, SoloRecord]] = []
        now_ts = datetime.now(timezone.utc).timestamp()

        for i, score in enumerate(doc_scores):
            if score <= 0:
                doc_text = (
                    f"{filtered[i].summary} {filtered[i].corrected_content} {filtered[i].weekday} "
                    f"{filtered[i].events} {filtered[i].period} {filtered[i].season} "
                    f"{'周末' if filtered[i].is_weekend else '工作日'} {filtered[i].sample_type} "
                    f"{filtered[i].trigger_scene} {filtered[i].break_point} "
                    f"{filtered[i].bridge_action} {filtered[i].environment_design} "
                    f"{filtered[i].next_experiment}"
                ).lower()
                if any(t in doc_text for t in query_tokens):
                    score = 0.1
                else:
                    continue

            record = filtered[i]
            summary_tokens = set(_tokenize_enhanced(record.summary))
            tag_tokens = set(_tokenize_enhanced(record.tags))
            for t in query_tokens:
                if t in summary_tokens:
                    score *= 1.5
                if t in tag_tokens:
                    score *= 1.2

            try:
                rec_date = datetime.fromisoformat(record.created_at or record.date)
                if rec_date.tzinfo is None:
                    rec_date = rec_date.replace(tzinfo=timezone.utc)
                age_days = (now_ts - rec_date.timestamp()) / 86400
            except ValueError:
                age_days = 0

            decay_factor = 0.5 ** (age_days / 90.0)
            final_score = score * max(0.2, decay_factor)
            scored.append((final_score, record))

        scored.sort(key=lambda item: -item[0])
        return [item[1] for item in scored[:limit]]

    def status(self) -> dict[str, object]:
        entry_count = self._db.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        record_count = self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
        pending_count = self._db.execute("SELECT COUNT(*) FROM pending_confirmations").fetchone()[0]
        todo_count = self._db.execute("SELECT COUNT(*) FROM todos").fetchone()[0]
        experiment_count = self._db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
        cur = self._db.execute("SELECT attachments FROM entries")
        attachment_count = sum(len(json.loads(row[0])) for row in cur.fetchall())
        last_row = self._db.execute(
            "SELECT created_at FROM entries ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        return {
            "entries": entry_count,
            "records": record_count,
            "attachments": attachment_count,
            "todos": todo_count,
            "experiments": experiment_count,
            "pending_confirmations": pending_count,
            "last_entry_at": last_row[0] if last_row else None,
            "path": str(self.root),
        }

    def dates_with_activity(self) -> set[str]:
        dates: set[str] = set()
        cur = self._db.execute("SELECT metadata, created_at FROM entries")
        for row in cur.fetchall():
            meta = json.loads(row[0]) if row[0] else {}
            date_str = str(meta.get("record_date") or row[1][:10])
            if date_str:
                dates.add(date_str)
        cur = self._db.execute("SELECT DISTINCT date FROM records")
        for row in cur.fetchall():
            if row[0]:
                dates.add(row[0])
        return dates

    def has_activity_on(self, target_date: str) -> bool:
        cur = self._db.execute("SELECT 1 FROM records WHERE date = ? LIMIT 1", (target_date,))
        if cur.fetchone():
            return True
        cur = self._db.execute(
            "SELECT 1 FROM entries WHERE created_at LIKE ? LIMIT 1",
            (f"{target_date}%",),
        )
        if cur.fetchone():
            return True
        cur = self._db.execute("SELECT metadata FROM entries")
        for row in cur.fetchall():
            meta = json.loads(row[0]) if row[0] else {}
            if meta.get("record_date") == target_date:
                return True
        return False

    def reminder_state(self) -> dict[str, int]:
        data = self._read_config()
        reminders = dict(data.get("reminders") or {})
        return {
            "last_pending_count": int(reminders.get("last_pending_count") or 0),
            "last_missing_streak": int(reminders.get("last_missing_streak") or 0),
        }

    def update_reminder_state(
        self,
        *,
        pending_count: int | None = None,
        missing_streak: int | None = None,
    ) -> None:
        data = self._read_config()
        reminders = dict(data.get("reminders") or {})
        if pending_count is not None:
            reminders["last_pending_count"] = pending_count
        if missing_streak is not None:
            reminders["last_missing_streak"] = missing_streak
        data["reminders"] = reminders
        from solo.core.workspace import get_config_path

        get_config_path(self.workspace).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # --- Row deserialization helpers ---

    @staticmethod
    def _row_to_entry(row: tuple) -> SoloEntry:
        return SoloEntry(
            id=row[0],
            content=row[1],
            created_at=row[2],
            channel=row[3],
            sender_id=row[4],
            chat_id=row[5],
            message_id=row[6],
            metadata=json.loads(row[7]) if row[7] else {},
            attachments=[
                StoredAttachment.from_dict(a)
                for a in json.loads(row[8]) if isinstance(a, dict)
            ],
        )

    @staticmethod
    def _row_to_record(row: tuple) -> SoloRecord:
        return SoloRecord(
            id=row[0],
            entry_id=row[1],
            date=row[2],
            raw_content=row[3],
            corrected_content=row[4],
            summary=row[5],
            tags=row[6],
            emotion=row[7],
            weekday=row[8],
            events=row[9],
            period=row[10],
            season=row[11],
            is_weekend=bool(row[12]),
            content_length=row[13],
            emotion_reason=row[14],
            related_people=row[15],
            related_places=row[16],
            source=row[17],
            created_at=row[18],
            attachments=[
                StoredAttachment.from_dict(a)
                for a in json.loads(row[19]) if isinstance(a, dict)
            ],
            sample_type=row[20],
            trigger_scene=row[21],
            friction_signal=row[22],
            awareness_timing=row[23],
            break_point=row[24],
            bridge_action=row[25],
            environment_design=row[26],
            next_experiment=row[27],
        )

    @staticmethod
    def _row_to_pending_confirmation(row: tuple) -> PendingConfirmation:
        return PendingConfirmation(
            id=row[0],
            entry_id=row[1],
            raw_content=row[2],
            clarification_reason=row[3],
            questions=json.loads(row[4]) if row[4] else [],
            created_at=row[5],
        )

    @staticmethod
    def _row_to_profile_update(row: tuple) -> ProfileUpdate:
        return ProfileUpdate(
            id=row[0], record_id=row[1], category=row[2], entity_type=row[3],
            entity_name=row[4], suggested_value=row[5], confidence=row[6], status=row[7],
        )

    @staticmethod
    def _row_to_report(row: tuple) -> SoloReport:
        return SoloReport(id=row[0], report_type=row[1], content=row[2], created_at=row[3])

    @staticmethod
    def _row_to_todo(row: tuple) -> SoloTodo:
        return SoloTodo(
            id=row[0], record_id=row[1], title=row[2], category=row[3],
            priority=row[4], due_date=row[5], status=row[6], source=row[7],
            created_at=row[8], completed_at=row[9],
        )

    @staticmethod
    def _row_to_experiment(row: tuple) -> SoloExperiment:
        return SoloExperiment(
            id=row[0],
            record_id=row[1],
            title=row[2],
            hypothesis=row[3],
            trigger=row[4],
            desired_action=row[5],
            environment_design=row[6],
            success_criteria=row[7],
            observation_window=row[8],
            status=row[9],
            source=row[10],
            created_at=row[11],
        )

    # --- Private helpers ---

    def _entry_date(self, entry: SoloEntry) -> str:
        metadata = entry.metadata or {}
        return str(metadata.get("record_date") or entry.created_at[:10])

    def _read_config(self) -> dict[str, Any]:
        from solo.core.workspace import get_config_path

        initialize_workspace(self.workspace)
        return dict(json.loads(get_config_path(self.workspace).read_text(encoding="utf-8")))

    def _persist_entry_attachments(
        self,
        entry_id: str,
        media: list[str],
        captured_at: str,
    ) -> list[StoredAttachment]:
        if not media:
            return []
        try:
            return persist_attachment_paths(
                media,
                workspace_root=Path(self.workspace),
                attachments_root=self.attachments_root,
                entry_id=entry_id,
                captured_at=captured_at,
            )
        except Exception:
            shutil.rmtree(self.attachments_root / "entries" / entry_id, ignore_errors=True)
            raise

    def _merge_entry_metadata(
        self,
        metadata: dict[str, Any] | None,
        source_context: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(metadata or {})
        if not source_context:
            return merged

        source_message = dict(merged.get("source_message") or {})
        if session_key := self._optional_text(source_context.get("session_key")):
            source_message["session_key"] = session_key
        if received_at := self._optional_text(source_context.get("received_at")):
            source_message["received_at"] = received_at
        message_metadata = source_context.get("message_metadata")
        if isinstance(message_metadata, dict) and message_metadata:
            source_message["metadata"] = dict(message_metadata)
        if source_message:
            merged["source_message"] = source_message
        return merged

    def _optional_text(self, value: object) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text or None


def _tokenize_enhanced(text: str) -> list[str]:
    """Tokenize text using Jieba for Chinese and regex for English."""
    if not text:
        return []

    import jieba
    import re

    text = text.lower()
    jieba_tokens = list(jieba.cut(text))
    ascii_tokens = re.findall(r"[a-z0-9]{2,}", text)
    return [t.strip() for t in jieba_tokens + ascii_tokens if t.strip()]
