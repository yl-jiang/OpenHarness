"""Data models for the standalone solo app."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from openharness.attachments import StoredAttachment
from pydantic import BaseModel, Field


class SoloHeartbeatConfig(BaseModel):
    """Periodic app-local heartbeat configuration."""

    enabled: bool = True
    interval_s: int = 30 * 60
    keep_recent_messages: int = 8


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

    @classmethod
    def from_json(cls, line: str) -> "SoloReport":
        return cls(**json.loads(line))

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


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
