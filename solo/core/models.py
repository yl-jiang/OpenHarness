"""Data models for the standalone solo app."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from feed_digest.config import FeedDigestConfig
from solo.core.attachments import StoredAttachment
from pydantic import BaseModel, Field


class SoloHeartbeatConfig(BaseModel):
    """Periodic app-local heartbeat configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60
    keep_recent_messages: int = 8
    quiet_hours_start: str = "22:30"
    quiet_hours_end: str = "08:00"
    timezone: str = "Asia/Shanghai"
    max_daily_pushes: int = 3


class SoloConfig(BaseModel):
    """Persistent solo app configuration."""

    version: int = 1
    provider_profile: str = "deepseek"
    enabled_channels: list[str] = Field(default_factory=list)
    channel_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    send_progress: bool = True
    send_tool_hints: bool = True
    heartbeat: SoloHeartbeatConfig = Field(default_factory=SoloHeartbeatConfig)
    log_level: str = "INFO"
    feed_digest: FeedDigestConfig = Field(default_factory=FeedDigestConfig)


class SoloState(BaseModel):
    """Runtime status snapshot for the solo app."""

    running: bool = False
    pid: int | None = None
    provider_profile: str = "deepseek"
    enabled_channels: list[str] = Field(default_factory=list)
    last_error: str | None = None


@dataclass(frozen=True)
class SoloEntry:
    """One raw solo entry captured before model structuring."""

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
    def from_json(cls, line: str) -> "SoloEntry":
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
class SoloRecord:
    """One structured solo record."""

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
    trigger_scene: str = ""
    friction_signal: str = ""
    awareness_timing: str = ""
    break_point: str = ""
    bridge_action: str = ""
    environment_design: str = ""
    next_experiment: str = ""

    @classmethod
    def from_json(cls, line: str) -> "SoloRecord":
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
            "trigger_scene": self.trigger_scene,
            "friction_signal": self.friction_signal,
            "awareness_timing": self.awareness_timing,
            "break_point": self.break_point,
            "bridge_action": self.bridge_action,
            "environment_design": self.environment_design,
            "next_experiment": self.next_experiment,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class PendingConfirmation:
    """A solo entry the agent refused to guess."""

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
    """A profile update suggested by the solo agent."""

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
class SoloReport:
    """A generated solo report."""

    id: str
    report_type: str
    content: str
    created_at: str
    period_start: str = ""
    period_end: str = ""
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, line: str) -> "SoloReport":
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
class SoloTodo:
    """A personal todo derived from solo records."""

    id: str
    record_id: str
    title: str
    category: str = ""
    priority: str = "medium"
    due_date: str = ""
    status: str = "pending"
    source: str = "derived"
    created_at: str = ""
    completed_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "SoloTodo":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "title": self.title,
            "category": self.category,
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
class SoloExperiment:
    """A behavior experiment derived from solo records."""

    id: str
    record_id: str
    title: str
    hypothesis: str = ""
    trigger: str = ""
    desired_action: str = ""
    environment_design: str = ""
    success_criteria: str = ""
    observation_window: str = ""
    status: str = "active"
    source: str = "derived"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "SoloExperiment":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "record_id": self.record_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "trigger": self.trigger,
            "desired_action": self.desired_action,
            "environment_design": self.environment_design,
            "success_criteria": self.success_criteria,
            "observation_window": self.observation_window,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProcessResult:
    """Summary of one solo processing run."""

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
    confidence: str = ""  # high | medium | low | empty for user
    status: str = "active"  # active | pending | rejected
    sort_order: int = 0
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
            "status": self.status, "sort_order": self.sort_order,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectAlias:
    id: str
    project_id: str
    alias: str
    source: str = "user"  # user | migration | ai
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


@dataclass(frozen=True)
class ProjectSuggestion:
    id: str
    suggestion_type: str  # link_entity | create_project | complete_milestone | ...
    project_id: str = ""
    title: str = ""
    rationale: str = ""
    proposed_payload_json: str = "{}"
    evidence_json: str = "[]"
    confidence: float = 0.0
    status: str = "pending"  # pending | accepted | rejected | snoozed | expired
    source: str = "ai"
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectSuggestion":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "suggestion_type": self.suggestion_type,
            "project_id": self.project_id, "title": self.title,
            "rationale": self.rationale,
            "proposed_payload_json": self.proposed_payload_json,
            "evidence_json": self.evidence_json,
            "confidence": self.confidence, "status": self.status,
            "source": self.source,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectSignal:
    id: str
    project_id: str
    signal_type: str  # progress | blocker | risk | decision | milestone_evidence | stale | momentum | scope_change
    summary: str
    severity: str = "info"  # info | warning | critical
    evidence_entity_type: str = ""
    evidence_entity_id: str = ""
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectSignal":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id,
            "signal_type": self.signal_type, "summary": self.summary,
            "severity": self.severity,
            "evidence_entity_type": self.evidence_entity_type,
            "evidence_entity_id": self.evidence_entity_id,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectSnapshot:
    id: str
    project_id: str
    snapshot_date: str
    summary: str = ""
    health: str = "normal"  # normal | attention | at_risk
    completion_pct: int | None = None
    activity_7d: int = 0
    open_blocker_count: int = 0
    next_action: str = ""
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectSnapshot":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id,
            "snapshot_date": self.snapshot_date, "summary": self.summary,
            "health": self.health, "completion_pct": self.completion_pct,
            "activity_7d": self.activity_7d,
            "open_blocker_count": self.open_blocker_count,
            "next_action": self.next_action,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class ProjectCheckin:
    id: str
    project_id: str
    channel: str = "onboard"
    question: str = ""
    status: str = "sent"  # sent | answered | dismissed
    response_record_id: str = ""
    created_at: str = ""
    responded_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "ProjectCheckin":
        return cls(**json.loads(line))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "project_id": self.project_id,
            "channel": self.channel, "question": self.question,
            "status": self.status,
            "response_record_id": self.response_record_id,
            "created_at": self.created_at,
            "responded_at": self.responded_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)
