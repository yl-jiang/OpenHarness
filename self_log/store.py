"""Append-only storage for the standalone self-log app."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from self_log.models import (
    PendingConfirmation,
    ProfileUpdate,
    SelfLogConfig,
    SelfLogEntry,
    SelfLogRecord,
    SelfLogReport,
)
from self_log.workspace import get_data_dir, initialize_workspace

ENTRIES_FILENAME = "entries.jsonl"
RECORDS_FILENAME = "records.jsonl"
PENDING_CONFIRMATIONS_FILENAME = "pending_confirmations.jsonl"
PROFILE_UPDATES_FILENAME = "profile_updates.jsonl"
REPORTS_FILENAME = "reports.jsonl"


class SelfLogStore:
    """Append-only self-log store rooted in the self-log workspace."""

    def __init__(self, workspace: str | Path | None = None) -> None:
        self.workspace = initialize_workspace(workspace)
        self.root = get_data_dir(self.workspace)
        self.entries_path = self.root / ENTRIES_FILENAME
        self.records_path = self.root / RECORDS_FILENAME
        self.pending_confirmations_path = self.root / PENDING_CONFIRMATIONS_FILENAME
        self.profile_updates_path = self.root / PROFILE_UPDATES_FILENAME
        self.reports_path = self.root / REPORTS_FILENAME

    def initialize(self) -> Path:
        initialize_workspace(self.workspace)
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (
            self.entries_path,
            self.records_path,
            self.pending_confirmations_path,
            self.profile_updates_path,
            self.reports_path,
        ):
            if not path.exists():
                path.write_text("", encoding="utf-8")
        return self.root

    def load_config(self) -> SelfLogConfig:
        from self_log.config import load_config

        return load_config(self.workspace)

    def save_config(self, config: SelfLogConfig) -> Path:
        from self_log.config import save_config

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
    ) -> SelfLogEntry:
        text = content.strip()
        if not text:
            raise ValueError("self-log content cannot be empty")
        self.initialize()
        entry = SelfLogEntry(
            id=uuid4().hex[:12],
            content=text,
            created_at=created_at or _now(),
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            message_id=message_id,
            metadata=metadata or {},
        )
        with self.entries_path.open("a", encoding="utf-8") as file:
            file.write(entry.to_json() + "\n")
        self.entries_path.chmod(0o600)
        return entry

    def list_entries(self, *, limit: int | None = None) -> list[SelfLogEntry]:
        entries = [SelfLogEntry.from_json(line) for line in self._read_jsonl(self.entries_path)]
        return entries if limit is None else entries[-limit:]

    def add_record(self, record: SelfLogRecord) -> None:
        self._append_jsonl(self.records_path, record.to_json())

    def list_records(self, *, limit: int | None = None) -> list[SelfLogRecord]:
        records = [SelfLogRecord.from_json(line) for line in self._read_jsonl(self.records_path)]
        return records if limit is None else records[-limit:]

    def update_record(self, record_id: str, **updates: Any) -> bool:
        """Update an existing record by ID with new field values."""
        records = self.list_records()
        updated = False
        new_records: list[SelfLogRecord] = []
        
        for r in records:
            if r.id == record_id:
                # Apply updates
                data = r.to_dict()
                data.update(updates)
                new_records.append(SelfLogRecord(**data))
                updated = True
            else:
                new_records.append(r)
        
        if updated:
            # Overwrite the file with updated list
            lines = [r.to_json() for r in new_records]
            self.records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            
        return updated

    def delete_record(self, record_id: str) -> bool:
        """Permanently delete a record by ID."""
        records = self.list_records()
        original_count = len(records)
        new_records = [r for r in records if r.id != record_id]
        
        if len(new_records) < original_count:
            # Overwrite the file
            lines = [r.to_json() for r in new_records]
            self.records_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
            
        return False

    def add_pending_confirmation(self, pending: PendingConfirmation) -> None:
        self._append_jsonl(self.pending_confirmations_path, pending.to_json())

    def list_pending_confirmations(self) -> list[PendingConfirmation]:
        return [
            PendingConfirmation.from_json(line)
            for line in self._read_jsonl(self.pending_confirmations_path)
        ]

    def add_profile_update(self, update: ProfileUpdate) -> None:
        self._append_jsonl(self.profile_updates_path, update.to_json())

    def list_profile_updates(self) -> list[ProfileUpdate]:
        return [ProfileUpdate.from_json(line) for line in self._read_jsonl(self.profile_updates_path)]

    def add_report(self, report: SelfLogReport) -> None:
        self._append_jsonl(self.reports_path, report.to_json())

    def list_reports(self) -> list[SelfLogReport]:
        return [SelfLogReport.from_json(line) for line in self._read_jsonl(self.reports_path)]

    def search_records(
        self,
        query: str | None = None,
        *,
        tags: list[str] | None = None,
        emotions: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 10,
    ) -> list[SelfLogRecord]:
        """Search records with filters and optimized ranking (BM25 + Temporal Decay)."""
        records = self.list_records()
        filtered: list[SelfLogRecord] = []

        # 1. Hard Filters (Date/Tag/Emotion)
        for record in records:
            if start_date and record.date < start_date:
                continue
            if end_date and record.date > end_date:
                continue
            if tags and not any(t.strip().lower() in record.tags.lower() for t in tags):
                continue
            if emotions and record.emotion not in emotions:
                continue
            filtered.append(record)

        if not filtered:
            return []

        if not query:
            return filtered[-limit:]

        # 2. Tokenization with Jieba
        query_tokens = _tokenize_enhanced(query)
        if not query_tokens:
            return filtered[-limit:]

        # BM25 Scoring using rank_bm25 package
        from rank_bm25 import BM25Okapi
        
        # Prepare corpus
        corpus_tokens = [
            _tokenize_enhanced(f"{r.summary} {r.corrected_content} {r.tags}")
            for r in filtered
        ]
        
        bm25 = BM25Okapi(corpus_tokens)
        doc_scores = bm25.get_scores(query_tokens)

        scored: list[tuple[float, SelfLogRecord]] = []
        now_ts = datetime.now(timezone.utc).timestamp()
        
        for i, score in enumerate(doc_scores):
            # rank_bm25 can return 0 or negative for common words in tiny corpus
            # We only care about positive matches for retrieval context
            if score <= 0:
                # If there's an exact match in summary or content even with 0 score (IDF issue),
                # we give it a tiny epsilon score so boosts can still work.
                doc_text = f"{filtered[i].summary} {filtered[i].corrected_content}".lower()
                if any(t in doc_text for t in query_tokens):
                    score = 0.1
                else:
                    continue
            
            record = filtered[i]
            
            # Boost matches in summary and tags
            summary_tokens = set(_tokenize_enhanced(record.summary))
            tag_tokens = set(_tokenize_enhanced(record.tags))
            for t in query_tokens:
                if t in summary_tokens:
                    score *= 1.5
                if t in tag_tokens:
                    score *= 1.2

            # 4. Temporal Decay (Recency bias)
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
        entries = self.list_entries()
        records = self.list_records()
        pending = self.list_pending_confirmations()
        return {
            "entries": len(entries),
            "records": len(records),
            "pending_confirmations": len(pending),
            "last_entry_at": entries[-1].created_at if entries else None,
            "path": str(self.root),
        }

    def dates_with_activity(self) -> set[str]:
        dates = {self._entry_date(entry) for entry in self.list_entries()}
        dates.update(record.date for record in self.list_records())
        return {item for item in dates if item}

    def has_activity_on(self, target_date: str) -> bool:
        return target_date in self.dates_with_activity()

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
        from self_log.workspace import get_config_path

        get_config_path(self.workspace).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _entry_date(self, entry: SelfLogEntry) -> str:
        metadata = entry.metadata or {}
        return str(metadata.get("record_date") or entry.created_at[:10])

    def _read_config(self) -> dict[str, Any]:
        from self_log.workspace import get_config_path

        initialize_workspace(self.workspace)
        return dict(json.loads(get_config_path(self.workspace).read_text(encoding="utf-8")))

    def _read_jsonl(self, path: Path) -> list[str]:
        self.initialize()
        return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def _append_jsonl(self, path: Path, line: str) -> None:
        self.initialize()
        with path.open("a", encoding="utf-8") as file:
            file.write(line + "\n")
        path.chmod(0o600)


def _tokenize_enhanced(text: str) -> list[str]:
    """Tokenize text using Jieba for Chinese and regex for English."""
    if not text:
        return []
    
    import jieba
    import re
    
    # 1. Clean and lower
    text = text.lower()
    
    # 2. Use Jieba for Chinese (standard mode)
    # This handles both individual characters and multi-character words correctly.
    jieba_tokens = list(jieba.cut(text))
    
    # 3. Use regex for English/Numbers to ensure consistency (length >= 2)
    ascii_tokens = re.findall(r"[a-z0-9]{2,}", text)
    
    # Combine and deduplicate within a document for tf counting later
    return [t.strip() for t in jieba_tokens + ascii_tokens if t.strip()]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
