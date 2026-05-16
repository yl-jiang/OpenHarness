"""Data models for the standalone self-log app."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from pydantic import BaseModel, Field


class SelfLogConfig(BaseModel):
    """Persistent self-log app configuration."""

    version: int = 1
    provider_profile: str = "codex"
    enabled_channels: list[str] = Field(default_factory=list)
    channel_configs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    send_progress: bool = True
    send_tool_hints: bool = True
    log_level: str = "INFO"


class SelfLogState(BaseModel):
    """Runtime status snapshot for the self-log app."""

    running: bool = False
    pid: int | None = None
    provider_profile: str = "codex"
    enabled_channels: list[str] = Field(default_factory=list)
    last_error: str | None = None


@dataclass(frozen=True)
class SelfLogEntry:
    """One raw self-log entry captured before model structuring."""

    id: str
    content: str
    created_at: str
    channel: str
    sender_id: str
    chat_id: str
    message_id: str | None = None
    metadata: dict[str, Any] | None = None

    @classmethod
    def from_json(cls, line: str) -> "SelfLogEntry":
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
            },
            ensure_ascii=False,
        )


@dataclass(frozen=True)
class SelfLogRecord:
    """One structured self-log record."""

    id: str
    entry_id: str
    date: str
    raw_content: str
    corrected_content: str
    summary: str
    tags: str
    emotion: str
    emotion_reason: str = ""
    related_people: str = ""
    related_places: str = ""
    source: str = "原始"
    created_at: str = ""

    @classmethod
    def from_json(cls, line: str) -> "SelfLogRecord":
        return cls(**json.loads(line))

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
            "emotion_reason": self.emotion_reason,
            "related_people": self.related_people,
            "related_places": self.related_places,
            "source": self.source,
            "created_at": self.created_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class PendingConfirmation:
    """A self-log entry the agent refused to guess."""

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
    """A profile update suggested by the self-log agent."""

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
class SelfLogReport:
    """A generated self-log report."""

    id: str
    report_type: str
    content: str
    created_at: str

    @classmethod
    def from_json(cls, line: str) -> "SelfLogReport":
        return cls(**json.loads(line))

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False)


@dataclass(frozen=True)
class ProcessResult:
    """Summary of one self-log processing run."""

    auto_processed: int
    pending_confirmations: int
    backfilled: bool = False
    backfill_date: str | None = None
    backfill_prompt: str | None = None
    consecutive_missing_days: int = 0
    pending_reminder: str | None = None
    missing_day_reminder: str | None = None
