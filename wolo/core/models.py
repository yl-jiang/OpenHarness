"""Data models for the standalone wolo app."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from feed_digest.config import FeedDigestConfig
from wolo.core.attachments import StoredAttachment
from pydantic import BaseModel, Field


class WoloHeartbeatConfig(BaseModel):
    """Periodic app-local heartbeat configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60
    keep_recent_messages: int = 8
    quiet_hours_start: str = "22:30"
    quiet_hours_end: str = "08:00"
    timezone: str = "Asia/Shanghai"
    max_daily_pushes: int = 3


class WoloConfig(BaseModel):
    """Persistent wolo app configuration."""

    version: int = 1
    provider_profile: str = "deepseek"
    enabled_channels: list[str] = Field(default_factory=list)
    channel_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    send_progress: bool = True
    send_tool_hints: bool = True
    heartbeat: WoloHeartbeatConfig = Field(default_factory=WoloHeartbeatConfig)
    log_level: str = "INFO"
    feed_digest: FeedDigestConfig = Field(default_factory=FeedDigestConfig)


class WoloState(BaseModel):
    """Runtime status snapshot for the wolo app."""

    running: bool = False
    pid: int | None = None
    provider_profile: str = "deepseek"
    enabled_channels: list[str] = Field(default_factory=list)
    last_error: str | None = None


@dataclass(frozen=True)
class WoloEntry:
    """One raw wolo entry captured before model structuring."""

    id: str
    content: str
    created_at: str
    channel: str
    sender_id: str
    chat_id: str
    message_id: str | None = None
    metadata: dict[str, Any] | None = None
    attachments: list[StoredAttachment] = field(default_factory=list)

    @classmethod
    def from_json(cls, line: str) -> "WoloEntry":
        data = json.loads(line)
        return cls(
            id=str(data["id"]),
            content=str(data["content"]),
            created_at=str(data["created_at"]),
            channel=str(data.get("channel", "local")),
            sender_id=str(data.get("sender_id", "")),
            chat_id=str(data.get("chat_id", "")),
            message_id=data.get("message_id"),
            metadata=dict(data.get("metadata") or {}),
            attachments=[
                StoredAttachment.from_dict(item)
                for item in data.get("attachments") or []
                if isinstance(item, dict)
            ],
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "content": self.content,
                "created_at": self.created_at,
                "channel": self.channel,
                "sender_id": self.sender_id,
                "chat_id": self.chat_id,
                "message_id": self.message_id,
                "metadata": self.metadata or {},
                "attachments": [item.to_dict() for item in self.attachments],
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class WoloRecord:
    """One structured wolo record."""

    id: str
    entry_id: str
    date: str
    raw_content: str
    corrected_content: str
    summary: str
    tags: str
    emotion: str
    weekday: str = ""
    events: str = ""
    period: str = ""
    season: str = ""
    is_weekend: bool = False
    content_length: int = 0
    emotion_reason: str = ""
    related_people: str = ""
    related_places: str = ""
    source: str = "原始"
    created_at: str = ""
    attachments: list[StoredAttachment] = field(default_factory=list)
    sample_type: str = "neutral"
    problem_essence: str = ""
    available_cards: str = ""
    strategy: str = ""
    next_move: str = ""
    deadline: str = ""
    validation_signal: str = ""

    @classmethod
    def from_json(cls, line: str) -> "WoloRecord":
        data = json.loads(line)
        data["attachments"] = [
            StoredAttachment.from_dict(item)
            for item in data.get("attachments") or []
            if isinstance(item, dict)
        ]
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "entry_id": self.entry_id,
            "date": self.date,
            "raw_content": self.raw_content,
            "corrected_content": self.corrected_content,
            "summary": self.summary,
            "tags": self.tags,
            "emotion": self.emotion,
            "weekday": self.weekday,
            "events": self.events,
            "period": self.period,
            "season": self.season,
            "is_weekend": self.is_weekend,
            "content_length": self.content_length,
            "emotion_reason": self.emotion_reason,
            "related_people": self.related_people,
            "related_places": self.related_places,
            "source": self.source,
            "created_at": self.created_at,
            "attachments": [item.to_dict() for item in self.attachments],
            "sample_type": self.sample_type,
            "problem_essence": self.problem_essence,
            "available_cards": self.available_cards,
            "strategy": self.strategy,
            "next_move": self.next_move,
            "deadline": self.deadline,
            "validation_signal": self.validation_signal,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class PendingConfirmation:
    """A wolo entry the agent refused to guess."""

    id: str
    entry_id: str
    raw_content: str
    clarification_reason: str
    questions: list[str]
    created_at: str

    @classmethod
    def from_json(cls, line: str) -> "PendingConfirmation":
        data = json.loads(line)
        return cls(
            id=str(data["id"]),
            entry_id=str(data["entry_id"]),
            raw_content=str(data["raw_content"]),
            clarification_reason=str(data.get("clarification_reason", "")),
            questions=list(data.get("questions") or []),
            created_at=str(data["created_at"]),
        )

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "entry_id": self.entry_id,
                "raw_content": self.raw_content,
                "clarification_reason": self.clarification_reason,
                "questions": self.questions,
                "created_at": self.created_at,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class ProfileUpdate:
    """A profile update suggested by the wolo agent."""

    id: str
    record_id: str
    category: str
    entity_type: str
    entity_name: str
    suggested_value: str
    confidence: str
    status: str = "pending"

    @classmethod
    def from_json(cls, line: str) -> "ProfileUpdate":
        return cls(**json.loads(line))

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


@dataclass(frozen=True)
class WoloReport:
    """A generated wolo report."""

    id: str
    report_type: str
    content: str
    created_at: str
    period_start: str = ""
    period_end: str = ""
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, line: str) -> "WoloReport":
        data = json.loads(line)
        data.setdefault("period_start", "")
        data.setdefault("period_end", "")
        data.setdefault("metadata", None)
        return cls(**data)

    def to_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "report_type": self.report_type,
                "content": self.content,
                "created_at": self.created_at,
                "period_start": self.period_start,
                "period_end": self.period_end,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class WoloTodo:
    """A work todo derived from wolo records."""

    id: str
    record_id: str
    title: str
    project: str = ""
    priority: str = "medium"
    due_date: str = ""
    status: str = "pending"
    source: str = "derived"
    created_at: str = ""
    completed_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "WoloTodo":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "title": self.title,
            "project": self.project,
            "priority": self.priority,
            "due_date": self.due_date,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class WoloDecision:
    """A decision derived from wolo records."""

    id: str
    record_id: str
    title: str
    rationale: str = ""
    impact: str = ""
    project: str = ""
    source: str = "derived"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "WoloDecision":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "title": self.title,
            "rationale": self.rationale,
            "impact": self.impact,
            "project": self.project,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class WoloHighlight:
    """An important work artifact derived from wolo records."""

    id: str
    record_id: str
    kind: str
    title: str
    content: str = ""
    project: str = ""
    tags: str = ""
    source: str = "derived"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "WoloHighlight":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "kind": self.kind,
            "title": self.title,
            "content": self.content,
            "project": self.project,
            "tags": self.tags,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class WoloExperiment:
    """A work experiment derived from wolo records."""

    id: str
    record_id: str
    title: str
    hypothesis: str = ""
    problem: str = ""
    strategy: str = ""
    next_move: str = ""
    success_signal: str = ""
    deadline: str = ""
    project: str = ""
    status: str = "active"
    source: str = "derived"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "WoloExperiment":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "problem": self.problem,
            "strategy": self.strategy,
            "next_move": self.next_move,
            "success_signal": self.success_signal,
            "deadline": self.deadline,
            "project": self.project,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProcessResult:
    """Summary of one wolo processing run."""

    auto_processed: int
    pending_confirmations: int
    backfilled: bool = False
    backfill_date: str | None = None
    backfill_prompt: str | None = None
    consecutive_missing_days: int = 0
    pending_reminder: str | None = None
    missing_day_reminder: str | None = None
    daily_question: str | None = None


@dataclass(frozen=True)
class Project:
    id: str
    title: str
    description: str = ""
    status: str = "active"  # active | completed | archived
    priority: str = "medium"  # high | medium | low
    start_date: str = ""
    target_date: str = ""
    completed_at: str = ""
    archived_at: str = ""
    archive_reason: str = ""
    tags: str = ""
    created_at: str = ""
    updated_at: str = ""
    stakeholders: str = ""
    success_criteria: str = ""

    @classmethod
    def from_json(cls, line: str) -> "Project":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "description": self.description,
            "status": self.status, "priority": self.priority,
            "start_date": self.start_date, "target_date": self.target_date,
            "completed_at": self.completed_at, "archived_at": self.archived_at,
            "archive_reason": self.archive_reason, "tags": self.tags,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "stakeholders": self.stakeholders, "success_criteria": self.success_criteria,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class Milestone:
    id: str
    project_id: str
    title: str
    description: str = ""
    status: str = "pending"  # pending | completed
    target_date: str = ""
    completed_at: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "Milestone":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id, "title": self.title,
            "description": self.description, "status": self.status,
            "target_date": self.target_date, "completed_at": self.completed_at,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectLink:
    id: str
    project_id: str
    entity_type: str  # record | todo | decision | highlight | experiment
    entity_id: str
    source: str = "user"  # user | ai_high_confidence | ai_candidate | migration
    confidence: str = ""
    status: str = "active"  # active | pending | rejected
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectLink":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id,
            "entity_type": self.entity_type, "entity_id": self.entity_id,
            "source": self.source, "confidence": self.confidence,
            "status": self.status, "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectAlias:
    id: str
    project_id: str
    alias: str
    source: str = "user"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectAlias":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id,
            "alias": self.alias, "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
