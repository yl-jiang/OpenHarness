"""SQLite-backed storage for the standalone solo app."""

from __future__ import annotations

from datetime import datetime, timezone, tzinfo
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
from common.project_ai.matcher import tokenize_enhanced as _tokenize_enhanced
from solo.core.models import (
    Milestone,
    PendingConfirmation,
    ProfileUpdate,
    Project,
    ProjectAlias,
    ProjectCheckin,
    ProjectLink,
    ProjectSignal,
    ProjectSnapshot,
    ProjectSuggestion,
    SoloEntry,
    SoloExperiment,
    SoloHealthRecord,
    SoloRecord,
    SoloReport,
    SoloTodo,
)
from solo.core.workspace import get_attachments_dir, get_data_dir, initialize_workspace
from solo.core.utils import _now

DB_FILENAME = "store.db"
_SCHEMA_VERSION = 7

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
    created_at TEXT NOT NULL,
    period_start TEXT NOT NULL DEFAULT '',
    period_end TEXT NOT NULL DEFAULT ''
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

CREATE TABLE IF NOT EXISTS llm_calls (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at);

CREATE TABLE IF NOT EXISTS vision_calls (
    id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    created_at TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_vision_calls_model ON vision_calls(model);
CREATE INDEX IF NOT EXISTS idx_vision_calls_created_at ON vision_calls(created_at);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    priority TEXT NOT NULL DEFAULT 'medium',
    start_date TEXT NOT NULL DEFAULT '',
    target_date TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT '',
    archive_reason TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);
CREATE INDEX IF NOT EXISTS idx_projects_target_date ON projects(target_date);
CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at);

CREATE TABLE IF NOT EXISTS milestones (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    target_date TEXT NOT NULL DEFAULT '',
    completed_at TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_milestones_project_id ON milestones(project_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status);

CREATE TABLE IF NOT EXISTS project_links (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    confidence TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_project_links_project_id ON project_links(project_id);
CREATE INDEX IF NOT EXISTS idx_project_links_entity ON project_links(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_project_links_status ON project_links(status);

CREATE TABLE IF NOT EXISTS project_aliases (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    alias TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    UNIQUE(project_id, alias)
);
CREATE INDEX IF NOT EXISTS idx_project_aliases_project_id ON project_aliases(project_id);
CREATE INDEX IF NOT EXISTS idx_project_aliases_alias ON project_aliases(alias);

CREATE TABLE IF NOT EXISTS project_suggestions (
    id TEXT PRIMARY KEY,
    suggestion_type TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    rationale TEXT NOT NULL DEFAULT '',
    proposed_payload_json TEXT NOT NULL DEFAULT '{}',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL DEFAULT 'ai',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_project_suggestions_status ON project_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_project_suggestions_project_id ON project_suggestions(project_id);

CREATE TABLE IF NOT EXISTS project_signals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    evidence_entity_type TEXT NOT NULL DEFAULT '',
    evidence_entity_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_signals_project_id ON project_signals(project_id);
CREATE INDEX IF NOT EXISTS idx_project_signals_signal_type ON project_signals(signal_type);

CREATE TABLE IF NOT EXISTS project_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    health TEXT NOT NULL DEFAULT 'normal',
    completion_pct INTEGER,
    activity_7d INTEGER NOT NULL DEFAULT 0,
    open_blocker_count INTEGER NOT NULL DEFAULT 0,
    next_action TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_snapshots_project_id ON project_snapshots(project_id);
CREATE INDEX IF NOT EXISTS idx_project_snapshots_date ON project_snapshots(snapshot_date);

CREATE TABLE IF NOT EXISTS project_checkins (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'onboard',
    question TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'sent',
    response_record_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    responded_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_project_checkins_project_id ON project_checkins(project_id);
CREATE INDEX IF NOT EXISTS idx_project_checkins_status ON project_checkins(status);

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

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def __del__(self) -> None:
        self.close()

    def _ensure_db(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path), timeout=10, check_same_thread=False
        )
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
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS llm_calls (
                id TEXT PRIMARY KEY,
                model TEXT NOT NULL,
                created_at TEXT NOT NULL,
                input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_model ON llm_calls(model)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_calls_created_at ON llm_calls(created_at)")
        llm_call_columns = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(llm_calls)").fetchall()
        }
        if "input_tokens" not in llm_call_columns:
            self._conn.execute(
                "ALTER TABLE llm_calls ADD COLUMN input_tokens INTEGER NOT NULL DEFAULT 0"
            )
        if "output_tokens" not in llm_call_columns:
            self._conn.execute(
                "ALTER TABLE llm_calls ADD COLUMN output_tokens INTEGER NOT NULL DEFAULT 0"
            )

        # Migrate reports table: add period_start/period_end/metadata columns
        report_cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(reports)").fetchall()
        }
        if "period_start" not in report_cols:
            self._conn.execute("ALTER TABLE reports ADD COLUMN period_start TEXT NOT NULL DEFAULT ''")
        if "period_end" not in report_cols:
            self._conn.execute("ALTER TABLE reports ADD COLUMN period_end TEXT NOT NULL DEFAULT ''")
        if "metadata" not in report_cols:
            self._conn.execute("ALTER TABLE reports ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'" )


        # Migration v5: project management tables
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                priority TEXT NOT NULL DEFAULT 'medium',
                start_date TEXT NOT NULL DEFAULT '',
                target_date TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                archived_at TEXT NOT NULL DEFAULT '',
                archive_reason TEXT NOT NULL DEFAULT '',
                tags TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_target_date ON projects(target_date)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS milestones (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                target_date TEXT NOT NULL DEFAULT '',
                completed_at TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_milestones_project_id ON milestones(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_milestones_status ON milestones(status)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_links (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                confidence TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, entity_type, entity_id)
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_links_project_id ON project_links(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_links_entity ON project_links(entity_type, entity_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_links_status ON project_links(status)")
        try:
            self._conn.execute("ALTER TABLE project_links ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        except Exception:
            pass
        try:
            self._conn.execute("ALTER TABLE milestones ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")
            self._conn.commit()
        except Exception:
            pass
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_aliases (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, alias)
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_aliases_project_id ON project_aliases(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_aliases_alias ON project_aliases(alias)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_suggestions (
                id TEXT PRIMARY KEY,
                suggestion_type TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                rationale TEXT NOT NULL DEFAULT '',
                proposed_payload_json TEXT NOT NULL DEFAULT '{}',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT 'ai',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_suggestions_status ON project_suggestions(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_suggestions_project_id ON project_suggestions(project_id)")

        # Migration v6: project signals, snapshots, checkins
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_signals (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                signal_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                evidence_entity_type TEXT NOT NULL DEFAULT '',
                evidence_entity_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_signals_project_id ON project_signals(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_signals_signal_type ON project_signals(signal_type)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_snapshots (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                health TEXT NOT NULL DEFAULT 'normal',
                completion_pct INTEGER,
                activity_7d INTEGER NOT NULL DEFAULT 0,
                open_blocker_count INTEGER NOT NULL DEFAULT 0,
                next_action TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_snapshots_project_id ON project_snapshots(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_snapshots_date ON project_snapshots(snapshot_date)")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS project_checkins (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                channel TEXT NOT NULL DEFAULT 'onboard',
                question TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                response_record_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                responded_at TEXT NOT NULL DEFAULT ''
            )"""
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_checkins_project_id ON project_checkins(project_id)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_project_checkins_status ON project_checkins(status)")

        # Migration v7: health_records table
        self._conn.executescript(
            """CREATE TABLE IF NOT EXISTS health_records (
                id TEXT PRIMARY KEY,
                record_id TEXT NOT NULL DEFAULT '',
                date TEXT NOT NULL,
                subject TEXT NOT NULL DEFAULT 'self',
                category TEXT NOT NULL,
                item TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                body_part TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                medication_name TEXT NOT NULL DEFAULT '',
                dosage TEXT NOT NULL DEFAULT '',
                frequency TEXT NOT NULL DEFAULT '',
                duration TEXT NOT NULL DEFAULT '',
                exercise_type TEXT NOT NULL DEFAULT '',
                exercise_duration_min INTEGER NOT NULL DEFAULT 0,
                exercise_intensity TEXT NOT NULL DEFAULT '',
                sleep_hours REAL NOT NULL DEFAULT 0,
                sleep_quality TEXT NOT NULL DEFAULT '',
                mood TEXT NOT NULL DEFAULT '',
                stress_level TEXT NOT NULL DEFAULT '',
                metrics_json TEXT NOT NULL DEFAULT '{}',
                tags TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'agent',
                linked_memory_id TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_health_records_date ON health_records(date);
            CREATE INDEX IF NOT EXISTS idx_health_records_subject ON health_records(subject);
            CREATE INDEX IF NOT EXISTS idx_health_records_category ON health_records(category);
            CREATE INDEX IF NOT EXISTS idx_health_records_status ON health_records(status);
            CREATE INDEX IF NOT EXISTS idx_health_records_record_id ON health_records(record_id);"""
        )

        try:
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS "
                "idx_entries_content_message_id_attachments "
                "ON entries(content, COALESCE(message_id, ''), attachments)"
            )
        except sqlite3.OperationalError:
            pass

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
        cols = ("id", "report_type", "content", "created_at", "period_start", "period_end", "metadata")
        vals = (
            r.id,
            r.report_type,
            r.content,
            r.created_at,
            r.period_start,
            r.period_end,
            json.dumps(r.metadata or {}, ensure_ascii=False),
        )
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


    @staticmethod
    def _project_to_row(p: Project):
        cols = (
            "id", "title", "description", "status", "priority", "start_date",
            "target_date", "completed_at", "archived_at", "archive_reason",
            "tags", "created_at", "updated_at",
        )
        vals = (
            p.id, p.title, p.description, p.status, p.priority, p.start_date,
            p.target_date, p.completed_at, p.archived_at, p.archive_reason,
            p.tags, p.created_at, p.updated_at,
        )
        return cols, vals

    @staticmethod
    def _milestone_to_row(m: Milestone):
        cols = (
            "id", "project_id", "title", "description", "status",
            "target_date", "completed_at", "sort_order", "created_at", "updated_at",
        )
        vals = (
            m.id, m.project_id, m.title, m.description, m.status,
            m.target_date, m.completed_at, m.sort_order, m.created_at, m.updated_at,
        )
        return cols, vals

    @staticmethod
    def _project_link_to_row(pl: ProjectLink):
        cols = (
            "id", "project_id", "entity_type", "entity_id", "source",
            "confidence", "status", "sort_order", "created_at", "updated_at",
        )
        vals = (
            pl.id, pl.project_id, pl.entity_type, pl.entity_id, pl.source,
            pl.confidence, pl.status, pl.sort_order, pl.created_at, pl.updated_at,
        )
        return cols, vals

    @staticmethod
    def _project_alias_to_row(pa: ProjectAlias):
        cols = ("id", "project_id", "alias", "source", "created_at")
        vals = (pa.id, pa.project_id, pa.alias, pa.source, pa.created_at)
        return cols, vals

    # --- Public API (unchanged signatures) ---

    def initialize(self) -> Path:
        initialize_workspace(self.workspace)
        self.root.mkdir(parents=True, exist_ok=True)
        _ = self._db  # ensure DB created
        return self.root

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
        resolved_message_id = self._optional_text(context.get("message_id") or message_id)
        entry_media = [str(item) for item in (context.get("media") or media or []) if str(item).strip()]
        dedup_attachments = json.dumps(entry_media, ensure_ascii=False)
        cur = self._db.execute(
            "SELECT id, content, created_at, channel, sender_id, chat_id, message_id, metadata, attachments "
            "FROM entries WHERE content = ? AND COALESCE(message_id, '') = ? AND attachments = ? LIMIT 1",
            (text, resolved_message_id or "", dedup_attachments),
        )
        existing = cur.fetchone()
        if existing is not None:
            return self._row_to_entry(existing)
        created_text = created_at or _now()
        entry_id = uuid4().hex[:12]
        attachments = self._persist_entry_attachments(entry_id, entry_media, created_text)
        entry = SoloEntry(
            id=entry_id,
            content=text,
            created_at=created_text,
            channel=str(context.get("channel") or channel),
            sender_id=str(context.get("sender_id") or sender_id),
            chat_id=str(context.get("chat_id") or chat_id),
            message_id=resolved_message_id,
            metadata=self._merge_entry_metadata(metadata, context),
            attachments=attachments,
        )
        try:
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
        except sqlite3.IntegrityError:
            cur = self._db.execute(
                "SELECT id, content, created_at, channel, sender_id, chat_id, message_id, metadata, attachments "
                "FROM entries WHERE content = ? AND COALESCE(message_id, '') = ? AND attachments = ? LIMIT 1",
                (text, resolved_message_id or "", dedup_attachments),
            )
            row = cur.fetchone()
            if row is not None:
                return self._row_to_entry(row)
            raise
        self._db.commit()
        return entry

    def record_llm_call(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        created_at: str | None = None,
    ) -> None:
        model_name = model.strip()
        if not model_name:
            raise ValueError("solo model name cannot be empty")
        self.initialize()
        prompt_tokens = max(0, int(input_tokens))
        completion_tokens = max(0, int(output_tokens))
        self._db.execute(
            "INSERT INTO llm_calls (id, model, created_at, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?)",
            (
                uuid4().hex[:12],
                model_name,
                created_at or _now(),
                prompt_tokens,
                completion_tokens,
            ),
        )
        self._db.commit()

    def record_vision_call(
        self,
        model: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        created_at: str | None = None,
    ) -> None:
        model_name = model.strip()
        if not model_name:
            raise ValueError("solo vision model name cannot be empty")
        self.initialize()
        prompt_tokens = max(0, int(input_tokens))
        completion_tokens = max(0, int(output_tokens))
        self._db.execute(
            "INSERT INTO vision_calls (id, model, created_at, input_tokens, output_tokens) VALUES (?, ?, ?, ?, ?)",
            (
                uuid4().hex[:12],
                model_name,
                created_at or _now(),
                prompt_tokens,
                completion_tokens,
            ),
        )
        self._db.commit()

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
        """Permanently delete a record and its cascaded children (todos, experiments, profile_updates)."""
        # Cascade: delete children referencing this record
        for child_table in ("todos", "experiments", "profile_updates"):
            self._db.execute(f"DELETE FROM {child_table} WHERE record_id = ?", (record_id,))
        self._db.execute("DELETE FROM project_links WHERE entity_type = 'record' AND entity_id = ?", (record_id,))
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

    def delete_report(self, report_id: str) -> bool:
        """Permanently delete a report by ID."""
        cur = self._db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
        self._db.commit()
        return cur.rowcount > 0

    def update_report(self, report_id: str, content: str) -> bool:
        """Update the content of an existing report."""
        cur = self._db.execute("UPDATE reports SET content = ? WHERE id = ?", (content, report_id))
        self._db.commit()
        return cur.rowcount > 0

    def get_report(self, report_id: str) -> SoloReport | None:
        """Get a single report by ID."""
        cur = self._db.execute("SELECT * FROM reports WHERE id = ?", (report_id,))
        row = cur.fetchone()
        return self._row_to_report(row) if row else None

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

    # ── Health records ──────────────────────────────────────────

    _HEALTH_RECORD_COLUMNS = [
        "id", "record_id", "date", "subject", "category", "item", "description",
        "body_part", "severity", "status", "medication_name", "dosage",
        "frequency", "duration", "exercise_type", "exercise_duration_min",
        "exercise_intensity", "sleep_hours", "sleep_quality", "mood",
        "stress_level", "metrics_json", "tags", "source", "linked_memory_id",
        "created_at", "updated_at",
    ]

    def _health_record_to_row(self, record: SoloHealthRecord) -> tuple[list[str], list[Any]]:
        cols = list(self._HEALTH_RECORD_COLUMNS)
        vals = [getattr(record, c) for c in cols]
        return cols, vals

    @staticmethod
    def _row_to_health_record(row: tuple) -> SoloHealthRecord:
        return SoloHealthRecord(
            id=row[0], record_id=row[1], date=row[2], subject=row[3],
            category=row[4], item=row[5], description=row[6], body_part=row[7],
            severity=row[8], status=row[9], medication_name=row[10],
            dosage=row[11], frequency=row[12], duration=row[13],
            exercise_type=row[14], exercise_duration_min=row[15],
            exercise_intensity=row[16], sleep_hours=row[17],
            sleep_quality=row[18], mood=row[19], stress_level=row[20],
            metrics_json=row[21], tags=row[22], source=row[23],
            linked_memory_id=row[24], created_at=row[25], updated_at=row[26],
        )

    def add_health_record(self, record: SoloHealthRecord) -> None:
        cols, vals = self._health_record_to_row(record)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO health_records ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def list_health_records(
        self, *, subject: str | None = None, category: str | None = None,
        status: str | None = None, date_from: str | None = None,
        date_to: str | None = None, limit: int | None = None,
    ) -> list[SoloHealthRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if subject:
            clauses.append("subject = ?")
            params.append(subject)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if date_from:
            clauses.append("date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("date <= ?")
            params.append(date_to)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        order = "ORDER BY date DESC, created_at DESC"
        if limit is not None:
            cur = self._db.execute(
                f"SELECT * FROM health_records{where} {order} LIMIT ?",
                params + [limit],
            )
        else:
            cur = self._db.execute(
                f"SELECT * FROM health_records{where} {order}", params
            )
        return [self._row_to_health_record(r) for r in cur.fetchall()]

    def get_health_record(self, record_id: str) -> SoloHealthRecord | None:
        cur = self._db.execute("SELECT * FROM health_records WHERE id = ?", (record_id,))
        row = cur.fetchone()
        return self._row_to_health_record(row) if row else None

    def update_health_record(self, health_id: str, **fields: Any) -> bool:
        allowed = set(self._HEALTH_RECORD_COLUMNS) - {"id"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        sets = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [health_id]
        cursor = self._db.execute(
            f"UPDATE health_records SET {sets} WHERE id = ?", values
        )
        self._db.commit()
        return cursor.rowcount > 0

    def delete_health_record(self, record_id: str) -> bool:
        cursor = self._db.execute("DELETE FROM health_records WHERE id = ?", (record_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def health_record_categories(self) -> dict[str, int]:
        rows = self._db.execute(
            "SELECT category, COUNT(*) FROM health_records GROUP BY category"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def health_record_subjects(self) -> dict[str, int]:
        """Return {subject: count}, e.g. {'self': 31, '小明': 13, '小红': 2}."""
        rows = self._db.execute(
            "SELECT subject, COUNT(*) FROM health_records GROUP BY subject"
        ).fetchall()
        return {row[0]: row[1] for row in rows}

    def start_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='in_progress' WHERE id=? AND status='pending'",
            (todo_id,),
        )
        self._db.commit()
        return cur.rowcount > 0

    def revert_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='pending' WHERE id=? AND status='in_progress'",
            (todo_id,),
        )
        self._db.commit()
        return cur.rowcount > 0

    def complete_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='done', completed_at=? WHERE id=? AND status != 'done'",
            (_now(), todo_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def reopen_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='pending', completed_at='' WHERE id=? AND status='done'",
            (todo_id,),
        )
        self._db.commit()
        return cur.rowcount > 0

    def cancel_todo(self, todo_id: str) -> bool:
        cur = self._db.execute(
            "UPDATE todos SET status='cancelled', completed_at=? WHERE id=? AND status NOT IN ('done', 'cancelled')",
            (_now(), todo_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def delete_todo(self, todo_id: str) -> bool:
        cur = self._db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
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

    def llm_usage_summary(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        target_tz: tzinfo | None = None,
    ) -> dict[str, object]:
        _all_calls = (
            "SELECT model, created_at, input_tokens, output_tokens FROM llm_calls "
            "UNION ALL "
            "SELECT model, created_at, input_tokens, output_tokens FROM vision_calls"
        )
        if start_date is None and end_date is None:
            cur = self._db.execute(
                "SELECT model, COUNT(*) AS count, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens "
                f"FROM ({_all_calls}) GROUP BY model ORDER BY count DESC, model ASC"
            )
            models = [
                {
                    "model": row[0],
                    "count": int(row[1]),
                    "input_tokens": int(row[2] or 0),
                    "output_tokens": int(row[3] or 0),
                }
                for row in cur.fetchall()
            ]
        else:
            zone = target_tz or datetime.now().astimezone().tzinfo or timezone.utc
            cur = self._db.execute(
                f"SELECT model, created_at, input_tokens, output_tokens "
                f"FROM ({_all_calls}) ORDER BY created_at ASC, model ASC"
            )
            aggregated: dict[str, dict[str, Any]] = {}
            for row in cur.fetchall():
                model = str(row[0] or "").strip()
                created_at = str(row[1] or "").strip()
                if not model or not created_at:
                    continue
                try:
                    call_at = datetime.fromisoformat(created_at)
                except ValueError:
                    continue
                if call_at.tzinfo is None:
                    call_at = call_at.replace(tzinfo=timezone.utc)
                day = call_at.astimezone(zone).date().isoformat()
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                item = aggregated.setdefault(
                    model,
                    {"model": model, "count": 0, "input_tokens": 0, "output_tokens": 0},
                )
                item["count"] += 1
                item["input_tokens"] += max(0, int(row[2] or 0))
                item["output_tokens"] += max(0, int(row[3] or 0))
            models = sorted(
                aggregated.values(),
                key=lambda item: (-int(item["count"]), str(item["model"])),
            )
        return {
            "total_calls": sum(item["count"] for item in models),
            "total_input_tokens": sum(item["input_tokens"] for item in models),
            "total_output_tokens": sum(item["output_tokens"] for item in models),
            "models": models,
        }

    def llm_token_daily_summary(
        self,
        *,
        start_date: str,
        end_date: str,
        target_tz: tzinfo | None = None,
    ) -> list[dict[str, object]]:
        zone = target_tz or datetime.now().astimezone().tzinfo or timezone.utc
        _all_calls = (
            "SELECT model, created_at, input_tokens, output_tokens FROM llm_calls "
            "UNION ALL "
            "SELECT model, created_at, input_tokens, output_tokens FROM vision_calls"
        )
        cur = self._db.execute(
            f"SELECT model, created_at, input_tokens, output_tokens "
            f"FROM ({_all_calls}) ORDER BY created_at ASC, model ASC"
        )
        daily: dict[tuple[str, str], dict[str, object]] = {}
        for row in cur.fetchall():
            model = str(row[0] or "").strip()
            created_at = str(row[1] or "").strip()
            if not model or not created_at:
                continue
            try:
                call_at = datetime.fromisoformat(created_at)
            except ValueError:
                continue
            if call_at.tzinfo is None:
                call_at = call_at.replace(tzinfo=timezone.utc)
            day = call_at.astimezone(zone).date().isoformat()
            if day < start_date or day > end_date:
                continue
            key = (day, model)
            point = daily.setdefault(
                key,
                {"date": day, "model": model, "input_tokens": 0, "output_tokens": 0},
            )
            point["input_tokens"] += max(0, int(row[2] or 0))
            point["output_tokens"] += max(0, int(row[3] or 0))
        return sorted(daily.values(), key=lambda item: (str(item["date"]), str(item["model"])))

    def llm_call_daily_summary(
        self,
        *,
        start_date: str,
        end_date: str,
        target_tz: tzinfo | None = None,
    ) -> list[dict[str, object]]:
        zone = target_tz or datetime.now().astimezone().tzinfo or timezone.utc
        _all_calls = (
            "SELECT model, created_at FROM llm_calls "
            "UNION ALL "
            "SELECT model, created_at FROM vision_calls"
        )
        cur = self._db.execute(
            f"SELECT model, created_at FROM ({_all_calls}) ORDER BY created_at ASC, model ASC"
        )
        daily: dict[tuple[str, str], dict[str, object]] = {}
        for row in cur.fetchall():
            model = str(row[0] or "").strip()
            created_at = str(row[1] or "").strip()
            if not model or not created_at:
                continue
            try:
                call_at = datetime.fromisoformat(created_at)
            except ValueError:
                continue
            if call_at.tzinfo is None:
                call_at = call_at.replace(tzinfo=timezone.utc)
            day = call_at.astimezone(zone).date().isoformat()
            if day < start_date or day > end_date:
                continue
            key = (day, model)
            point = daily.setdefault(key, {"date": day, "model": model, "count": 0})
            point["count"] += 1
        return sorted(daily.values(), key=lambda item: (str(item["date"]), str(item["model"])))

    def vision_usage_summary(
        self,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        target_tz: tzinfo | None = None,
    ) -> dict[str, object]:
        if start_date is None and end_date is None:
            cur = self._db.execute(
                "SELECT model, COUNT(*) AS count, "
                "COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens "
                "FROM vision_calls GROUP BY model ORDER BY count DESC, model ASC"
            )
            models = [
                {
                    "model": row[0],
                    "count": int(row[1]),
                    "input_tokens": int(row[2] or 0),
                    "output_tokens": int(row[3] or 0),
                }
                for row in cur.fetchall()
            ]
        else:
            zone = target_tz or datetime.now().astimezone().tzinfo or timezone.utc
            cur = self._db.execute(
                "SELECT model, created_at, input_tokens, output_tokens "
                "FROM vision_calls ORDER BY created_at ASC, model ASC"
            )
            aggregated: dict[str, dict[str, Any]] = {}
            for row in cur.fetchall():
                model = str(row[0] or "").strip()
                created_at = str(row[1] or "").strip()
                if not model or not created_at:
                    continue
                try:
                    call_at = datetime.fromisoformat(created_at)
                except ValueError:
                    continue
                if call_at.tzinfo is None:
                    call_at = call_at.replace(tzinfo=timezone.utc)
                day = call_at.astimezone(zone).date().isoformat()
                if start_date and day < start_date:
                    continue
                if end_date and day > end_date:
                    continue
                item = aggregated.setdefault(
                    model,
                    {"model": model, "count": 0, "input_tokens": 0, "output_tokens": 0},
                )
                item["count"] += 1
                item["input_tokens"] += max(0, int(row[2] or 0))
                item["output_tokens"] += max(0, int(row[3] or 0))
            models = sorted(
                aggregated.values(),
                key=lambda item: (-int(item["count"]), str(item["model"])),
            )
        return {
            "total_calls": sum(item["count"] for item in models),
            "total_input_tokens": sum(item["input_tokens"] for item in models),
            "total_output_tokens": sum(item["output_tokens"] for item in models),
            "models": models,
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
        import json as _json

        return SoloReport(
            id=row[0], report_type=row[1], content=row[2], created_at=row[3],
            period_start=row[4] if len(row) > 4 else "",
            period_end=row[5] if len(row) > 5 else "",
            metadata=_json.loads(row[6]) if len(row) > 6 and row[6] else None,
        )

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


    @staticmethod
    def _row_to_project(row: tuple) -> Project:
        return Project(
            id=row[0], title=row[1], description=row[2], status=row[3],
            priority=row[4], start_date=row[5], target_date=row[6],
            completed_at=row[7], archived_at=row[8], archive_reason=row[9],
            tags=row[10], created_at=row[11], updated_at=row[12],
        )

    @staticmethod
    def _row_to_milestone(row: tuple) -> Milestone:
        return Milestone(
            id=row[0], project_id=row[1], title=row[2], description=row[3],
            status=row[4], target_date=row[5], completed_at=row[6],
            sort_order=row[7], created_at=row[8], updated_at=row[9],
        )

    @staticmethod
    def _row_to_project_link(row: tuple) -> ProjectLink:
        return ProjectLink(
            id=row[0], project_id=row[1], entity_type=row[2], entity_id=row[3],
            source=row[4], confidence=row[5], status=row[6],
            sort_order=row[7], created_at=row[8], updated_at=row[9],
        )

    @staticmethod
    def _row_to_project_alias(row: tuple) -> ProjectAlias:
        return ProjectAlias(
            id=row[0], project_id=row[1], alias=row[2], source=row[3],
            created_at=row[4],
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


    # --- Project CRUD ---

    def create_project(self, project: Project) -> None:
        cols, vals = self._project_to_row(project)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO projects ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def update_project(self, project_id: str, **fields: Any) -> bool:
        allowed = {
            "title", "description", "status", "priority", "start_date",
            "target_date", "completed_at", "archived_at", "archive_reason",
            "tags", "updated_at",
        }
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        params.append(_now())
        params.append(project_id)
        cur = self._db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id=?", params
        )
        self._db.commit()
        return cur.rowcount > 0

    def delete_project(self, project_id: str) -> bool:
        cur = self._db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        self._db.commit()
        return cur.rowcount > 0

    def complete_project(self, project_id: str) -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE projects SET status='completed', completed_at=?, updated_at=? WHERE id=? AND status='active'",
            (now, now, project_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def archive_project(self, project_id: str, reason: str = "") -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE projects SET status='archived', archived_at=?, archive_reason=?, updated_at=? WHERE id=?",
            (now, reason, now, project_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def reactivate_project(self, project_id: str) -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE projects SET status='active', completed_at='', archived_at='', archive_reason='', updated_at=? WHERE id=?",
            (now, project_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def get_project(self, project_id: str) -> Project | None:
        cur = self._db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = cur.fetchone()
        return self._row_to_project(row) if row else None

    def list_projects(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Project]:
        clauses: list[str] = []
        params: list[Any] = []
        if status and status != "all":
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM projects{where} ORDER BY updated_at DESC, rowid DESC"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(offset)
        cur = self._db.execute(sql, params)
        return [self._row_to_project(row) for row in cur.fetchall()]

    # --- Milestone CRUD ---

    def create_milestone(self, milestone: Milestone) -> None:
        cols, vals = self._milestone_to_row(milestone)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO milestones ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def update_milestone(self, milestone_id: str, **fields: Any) -> bool:
        allowed = {
            "title", "description", "status", "target_date",
            "completed_at", "sort_order", "updated_at",
        }
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        params.append(_now())
        params.append(milestone_id)
        cur = self._db.execute(
            f"UPDATE milestones SET {', '.join(sets)} WHERE id=?", params
        )
        self._db.commit()
        return cur.rowcount > 0

    def complete_milestone(self, milestone_id: str) -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE milestones SET status='completed', completed_at=?, updated_at=? WHERE id=? AND status='pending'",
            (now, now, milestone_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def delete_milestone(self, milestone_id: str) -> bool:
        cur = self._db.execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
        self._db.commit()
        return cur.rowcount > 0

    def reorder_milestones(self, project_id: str, milestone_ids: list[str]) -> None:
        for idx, milestone_id in enumerate(milestone_ids):
            self._db.execute(
                "UPDATE milestones SET sort_order = ? WHERE project_id = ? AND id = ?",
                (idx, project_id, milestone_id),
            )
        self._db.commit()

    def list_milestones(self, project_id: str) -> list[Milestone]:
        cur = self._db.execute(
            "SELECT id, project_id, title, description, status, target_date, completed_at, sort_order, created_at, updated_at FROM milestones WHERE project_id = ? ORDER BY sort_order, rowid",
            (project_id,),
        )
        return [self._row_to_milestone(row) for row in cur.fetchall()]

    # --- ProjectLink CRUD ---

    def create_project_link(self, link: ProjectLink) -> None:
        cols, vals = self._project_link_to_row(link)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO project_links ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def update_project_link(self, link_id: str, **fields: Any) -> bool:
        allowed = {
            "entity_type", "entity_id", "source", "confidence",
            "status", "updated_at",
        }
        sets = []
        params: list[Any] = []
        for k, v in fields.items():
            if k in allowed:
                sets.append(f"{k}=?")
                params.append(v)
        if not sets:
            return False
        sets.append("updated_at=?")
        params.append(_now())
        params.append(link_id)
        cur = self._db.execute(
            f"UPDATE project_links SET {', '.join(sets)} WHERE id=?", params
        )
        self._db.commit()
        return cur.rowcount > 0

    def delete_project_link(self, link_id: str) -> bool:
        cur = self._db.execute("DELETE FROM project_links WHERE id = ?", (link_id,))
        self._db.commit()
        return cur.rowcount > 0

    def list_project_links(
        self,
        *,
        project_id: str | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        status: str | None = None,
    ) -> list[ProjectLink]:
        clauses: list[str] = []
        params: list[Any] = []
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if entity_type:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if entity_id:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        _pl_cols = "id, project_id, entity_type, entity_id, source, confidence, status, sort_order, created_at, updated_at"
        cur = self._db.execute(
            f"SELECT {_pl_cols} FROM project_links{where} ORDER BY sort_order, rowid", params
        )
        return [self._row_to_project_link(row) for row in cur.fetchall()]

    def reorder_project_links(self, project_id: str, link_ids: list[str]) -> None:
        for idx, link_id in enumerate(link_ids):
            self._db.execute(
                "UPDATE project_links SET sort_order = ? WHERE project_id = ? AND id = ?",
                (idx, project_id, link_id),
            )
        self._db.commit()

    def accept_project_link(self, link_id: str) -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE project_links SET status='active', updated_at=? WHERE id=? AND status='pending'",
            (now, link_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def reject_project_link(self, link_id: str) -> bool:
        now = _now()
        cur = self._db.execute(
            "UPDATE project_links SET status='rejected', updated_at=? WHERE id=? AND status IN ('pending', 'active')",
            (now, link_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    # --- ProjectAlias CRUD ---

    def create_project_alias(self, alias: ProjectAlias) -> None:
        cols, vals = self._project_alias_to_row(alias)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO project_aliases ({', '.join(cols)}) VALUES ({placeholders})", vals
        )
        self._db.commit()

    def delete_project_alias(self, alias_id: str) -> bool:
        cur = self._db.execute("DELETE FROM project_aliases WHERE id = ?", (alias_id,))
        self._db.commit()
        return cur.rowcount > 0

    def list_project_aliases(self, project_id: str) -> list[ProjectAlias]:
        cur = self._db.execute(
            "SELECT * FROM project_aliases WHERE project_id = ? ORDER BY rowid",
            (project_id,),
        )
        return [self._row_to_project_alias(row) for row in cur.fetchall()]

    def resolve_entity_summary(self, entity_type: str, entity_id: str) -> str:
        """Return a human-readable summary for a linked entity."""
        if entity_type == "record":
            cur = self._db.execute("SELECT summary FROM records WHERE id = ?", (entity_id,))
        elif entity_type == "todo":
            cur = self._db.execute("SELECT title FROM todos WHERE id = ?", (entity_id,))
        elif entity_type == "experiment":
            cur = self._db.execute("SELECT title FROM experiments WHERE id = ?", (entity_id,))
        else:
            return ""
        row = cur.fetchone()
        return row[0] if row else ""

    def entity_exists(self, entity_type: str, entity_id: str) -> bool:
        """Check whether a linked entity still exists in its table."""
        if entity_type == "record":
            cur = self._db.execute("SELECT 1 FROM records WHERE id = ?", (entity_id,))
        elif entity_type == "todo":
            cur = self._db.execute("SELECT 1 FROM todos WHERE id = ?", (entity_id,))
        elif entity_type == "experiment":
            cur = self._db.execute("SELECT 1 FROM experiments WHERE id = ?", (entity_id,))
        else:
            return False
        return cur.fetchone() is not None

    # --- ProjectSuggestion CRUD ---

    def create_project_suggestion(self, suggestion: ProjectSuggestion) -> None:
        cols, vals = self._project_suggestion_to_row(suggestion)
        placeholders = ", ".join("?" * len(vals))
        self._db.execute(
            f"INSERT INTO project_suggestions ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        self._db.commit()

    def list_project_suggestions(
        self,
        *,
        status: str | None = None,
        project_id: str | None = None,
        suggestion_type: str | None = None,
        limit: int | None = None,
    ) -> list[ProjectSuggestion]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if suggestion_type is not None:
            clauses.append("suggestion_type = ?")
            params.append(suggestion_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        lim = f"LIMIT {limit}" if limit else ""
        cur = self._db.execute(
            f"SELECT * FROM project_suggestions {where} ORDER BY confidence DESC, rowid DESC {lim}",
            params,
        )
        return [self._row_to_project_suggestion(row) for row in cur.fetchall()]

    def update_project_suggestion(self, suggestion_id: str, **fields: Any) -> bool:
        allowed = {"status", "rationale", "title", "confidence", "updated_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = self._db.execute(
            f"UPDATE project_suggestions SET {set_clause} WHERE id = ?",
            (*updates.values(), suggestion_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def accept_project_suggestion(self, suggestion_id: str) -> bool:
        return self.update_project_suggestion(suggestion_id, status="accepted")

    def reject_project_suggestion(self, suggestion_id: str) -> bool:
        return self.update_project_suggestion(suggestion_id, status="rejected")

    def snooze_project_suggestion(self, suggestion_id: str) -> bool:
        return self.update_project_suggestion(suggestion_id, status="snoozed")

    @staticmethod
    def _project_suggestion_to_row(s: ProjectSuggestion) -> tuple[list[str], list[Any]]:
        cols = [
            "id", "suggestion_type", "project_id", "title", "rationale",
            "proposed_payload_json", "evidence_json", "confidence", "status",
            "source", "created_at", "updated_at",
        ]
        vals = [
            s.id, s.suggestion_type, s.project_id, s.title, s.rationale,
            s.proposed_payload_json, s.evidence_json, s.confidence, s.status,
            s.source, s.created_at, s.updated_at,
        ]
        return cols, vals

    @staticmethod
    def _row_to_project_suggestion(row: tuple) -> ProjectSuggestion:
        keys = [
            "id", "suggestion_type", "project_id", "title", "rationale",
            "proposed_payload_json", "evidence_json", "confidence", "status",
            "source", "created_at", "updated_at",
        ]
        return ProjectSuggestion(**dict(zip(keys, row)))

    # --- ProjectSignal CRUD ---

    def create_project_signal(self, signal: ProjectSignal) -> None:
        self._db.execute(
            "INSERT INTO project_signals "
            "(id, project_id, signal_type, summary, severity, "
            "evidence_entity_type, evidence_entity_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                signal.id, signal.project_id, signal.signal_type,
                signal.summary, signal.severity,
                signal.evidence_entity_type, signal.evidence_entity_id,
                signal.created_at,
            ),
        )
        self._db.commit()

    def list_project_signals(
        self,
        project_id: str,
        *,
        signal_type: str | None = None,
        limit: int | None = None,
    ) -> list[ProjectSignal]:
        clauses: list[str] = ["project_id = ?"]
        params: list[Any] = [project_id]
        if signal_type is not None:
            clauses.append("signal_type = ?")
            params.append(signal_type)
        where = f"WHERE {' AND '.join(clauses)}"
        lim = f"LIMIT {limit}" if limit else ""
        cur = self._db.execute(
            f"SELECT * FROM project_signals {where} ORDER BY rowid DESC {lim}",
            params,
        )
        return [self._row_to_project_signal(row) for row in cur.fetchall()]

    def delete_project_signal(self, signal_id: str) -> bool:
        cur = self._db.execute("DELETE FROM project_signals WHERE id = ?", (signal_id,))
        self._db.commit()
        return cur.rowcount > 0

    @staticmethod
    def _row_to_project_signal(row: tuple) -> ProjectSignal:
        keys = [
            "id", "project_id", "signal_type", "summary", "severity",
            "evidence_entity_type", "evidence_entity_id", "created_at",
        ]
        return ProjectSignal(**dict(zip(keys, row)))

    # --- ProjectSnapshot CRUD ---

    def create_project_snapshot(self, snapshot: ProjectSnapshot) -> None:
        self._db.execute(
            "INSERT INTO project_snapshots "
            "(id, project_id, snapshot_date, summary, health, completion_pct, "
            "activity_7d, open_blocker_count, next_action, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot.id, snapshot.project_id, snapshot.snapshot_date,
                snapshot.summary, snapshot.health, snapshot.completion_pct,
                snapshot.activity_7d, snapshot.open_blocker_count,
                snapshot.next_action, snapshot.created_at,
            ),
        )
        self._db.commit()

    def list_project_snapshots(
        self,
        project_id: str,
        *,
        limit: int | None = None,
    ) -> list[ProjectSnapshot]:
        lim = f"LIMIT {limit}" if limit else ""
        cur = self._db.execute(
            f"SELECT * FROM project_snapshots WHERE project_id = ? "
            f"ORDER BY snapshot_date DESC {lim}",
            (project_id,),
        )
        return [self._row_to_project_snapshot(row) for row in cur.fetchall()]

    def get_latest_project_snapshot(self, project_id: str) -> ProjectSnapshot | None:
        cur = self._db.execute(
            "SELECT * FROM project_snapshots WHERE project_id = ? "
            "ORDER BY snapshot_date DESC LIMIT 1",
            (project_id,),
        )
        row = cur.fetchone()
        return self._row_to_project_snapshot(row) if row else None

    @staticmethod
    def _row_to_project_snapshot(row: tuple) -> ProjectSnapshot:
        keys = [
            "id", "project_id", "snapshot_date", "summary", "health",
            "completion_pct", "activity_7d", "open_blocker_count",
            "next_action", "created_at",
        ]
        return ProjectSnapshot(**dict(zip(keys, row)))

    # --- ProjectCheckin CRUD ---

    def create_project_checkin(self, checkin: ProjectCheckin) -> None:
        self._db.execute(
            "INSERT INTO project_checkins "
            "(id, project_id, channel, question, status, "
            "response_record_id, created_at, responded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                checkin.id, checkin.project_id, checkin.channel,
                checkin.question, checkin.status,
                checkin.response_record_id, checkin.created_at,
                checkin.responded_at,
            ),
        )
        self._db.commit()

    def list_project_checkins(
        self,
        project_id: str,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[ProjectCheckin]:
        clauses: list[str] = ["project_id = ?"]
        params: list[Any] = [project_id]
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}"
        lim = f"LIMIT {limit}" if limit else ""
        cur = self._db.execute(
            f"SELECT * FROM project_checkins {where} ORDER BY rowid DESC {lim}",
            params,
        )
        return [self._row_to_project_checkin(row) for row in cur.fetchall()]

    def update_project_checkin(self, checkin_id: str, **fields: Any) -> bool:
        allowed = {"status", "response_record_id", "responded_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        cur = self._db.execute(
            f"UPDATE project_checkins SET {set_clause} WHERE id = ?",
            (*updates.values(), checkin_id),
        )
        self._db.commit()
        return cur.rowcount > 0

    def get_recent_checkin_question(self, project_id: str, *, days: int = 7) -> str | None:
        """Return the most recent checkin question for a project within the last N days."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        cur = self._db.execute(
            "SELECT question FROM project_checkins "
            "WHERE project_id = ? AND created_at >= ? "
            "ORDER BY rowid DESC LIMIT 1",
            (project_id, cutoff),
        )
        row = cur.fetchone()
        return row[0] if row else None

    @staticmethod
    def _row_to_project_checkin(row: tuple) -> ProjectCheckin:
        keys = [
            "id", "project_id", "channel", "question", "status",
            "response_record_id", "created_at", "responded_at",
        ]
        return ProjectCheckin(**dict(zip(keys, row)))

    # --- Derived fields ---

    def get_project_detail(self, project_id: str) -> dict | None:
        project = self.get_project(project_id)
        if project is None:
            return None
        detail = project.to_dict()

        milestones = self.list_milestones(project_id)
        milestone_count = len(milestones)
        completed_milestone_count = sum(1 for m in milestones if m.status == "completed")

        links = self.list_project_links(project_id=project_id, status="active")
        linked_todo_ids = [lnk.entity_id for lnk in links if lnk.entity_type == "todo"]
        linked_record_count = sum(1 for lnk in links if lnk.entity_type == "record")
        linked_todo_count = len(linked_todo_ids)

        # Calculate completion from linked todos
        completed_linked_todo_count = 0
        if linked_todo_ids:
            placeholders = ", ".join("?" * len(linked_todo_ids))
            cur = self._db.execute(
                f"SELECT id, status FROM todos WHERE id IN ({placeholders})",
                linked_todo_ids,
            )
            for row in cur.fetchall():
                if row[1] == "done":
                    completed_linked_todo_count += 1

        # Completion percentage
        completion_pct: int | None = None
        completion_source = "none"
        if milestone_count > 0:
            completion_pct = int(round(completed_milestone_count / milestone_count * 100))
            completion_source = "milestones"
        elif linked_todo_count > 0:
            completion_pct = int(round(completed_linked_todo_count / linked_todo_count * 100))
            completion_source = "todos"

        detail["completion_pct"] = completion_pct
        detail["completion_source"] = completion_source
        detail["milestone_count"] = milestone_count
        detail["completed_milestone_count"] = completed_milestone_count
        detail["linked_record_count"] = linked_record_count
        detail["linked_todo_count"] = linked_todo_count
        detail["completed_linked_todo_count"] = completed_linked_todo_count

        # Activity counts
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        cutoff_7d = (now - timedelta(days=7)).isoformat()
        cutoff_30d = (now - timedelta(days=30)).isoformat()

        all_links = self.list_project_links(project_id=project_id)
        activity_7d = sum(
            1 for lnk in all_links if lnk.created_at and lnk.created_at >= cutoff_7d
        )
        activity_30d = sum(
            1 for lnk in all_links if lnk.created_at and lnk.created_at >= cutoff_30d
        )
        detail["activity_7d"] = activity_7d
        detail["activity_30d"] = activity_30d

        # Last activity
        timestamps = [lnk.created_at for lnk in all_links if lnk.created_at]
        if milestones:
            timestamps.extend(m.created_at for m in milestones if m.created_at)
            timestamps.extend(m.completed_at for m in milestones if m.completed_at)
        detail["last_activity_at"] = max(timestamps) if timestamps else ""

        # Risk status
        risk_status = "normal"
        if project.target_date and project.status != "completed":
            try:
                target = datetime.fromisoformat(project.target_date).date()
                today = now.date()
                if target < today:
                    risk_status = "at_risk"
                elif target <= today + timedelta(days=7):
                    if completion_pct is None or completion_pct < 80:
                        risk_status = "attention"
            except ValueError:
                pass
        if risk_status == "normal" and activity_30d == 0 and all_links:
            # 30 days no activity and there are links
            if detail["last_activity_at"]:
                try:
                    last = datetime.fromisoformat(detail["last_activity_at"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if (now - last).days >= 30:
                        risk_status = "attention"
                except ValueError:
                    pass
        detail["risk_status"] = risk_status

        # Open blocker count (solo has no highlight/blocker concept)
        detail["open_blocker_count"] = 0

        return detail

    def list_projects_with_detail(
        self,
        *,
        status: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        projects = self.list_projects(status=status, limit=limit, offset=offset)
        return [
            self.get_project_detail(p.id)  # type: ignore[misc]
            for p in projects
            if self.get_project_detail(p.id) is not None
        ]


