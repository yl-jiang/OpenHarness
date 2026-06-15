"""Shared types for project AI features.

These are plain dicts / dataclasses used by both solo and wolo domains.
Domain-specific models (ProjectSuggestion, etc.) live in their own models.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


# Suggestion type constants
SUGGEST_LINK_ENTITY = "link_entity"
SUGGEST_CREATE_PROJECT = "create_project"
SUGGEST_COMPLETE_MILESTONE = "complete_milestone"
SUGGEST_CREATE_MILESTONE = "create_milestone"
SUGGEST_UPDATE_PROJECT = "update_project"
SUGGEST_ARCHIVE_PROJECT = "archive_project"
SUGGEST_REACTIVATE_PROJECT = "reactivate_project"
SUGGEST_MERGE_PROJECTS = "merge_projects"
SUGGEST_SPLIT_PROJECT = "split_project"
SUGGEST_ASK_FOLLOWUP = "ask_followup"

# Suggestion status constants
STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_SNOOZED = "snoozed"
STATUS_EXPIRED = "expired"

# Confidence thresholds for ProjectLinker actions
CONFIDENCE_AUTO_LINK = 0.85   # >= this: auto-create active ProjectLink
CONFIDENCE_SUGGEST = 0.55     # >= this: create pending suggestion
# < CONFIDENCE_SUGGEST: discard


class ProjectLinkInput(BaseModel):
    """Minimal project representation used by the linker pipeline."""

    id: str
    title: str = ""
    description: str = ""


class LlmEvidenceItem(BaseModel):
    """Evidence item returned by the LLM linker."""

    entity_type: str
    entity_id: str = ""


class LlmMatchItem(BaseModel):
    """Single project match returned by the LLM linker."""

    project_id: str
    project_title: str = ""
    confidence: float
    rationale: str = ""
    evidence: list[LlmEvidenceItem] = Field(default_factory=list)


class LlmMatchResponse(BaseModel):
    """Top-level JSON response from the LLM linker."""

    matches: list[LlmMatchItem] = Field(default_factory=list)


@dataclass
class MatchCandidate:
    """A potential match between an entity and a project."""

    project_id: str
    project_title: str
    confidence: float
    strategy: str  # deterministic | llm | hybrid
    evidence: list[dict[str, str]] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_title": self.project_title,
            "confidence": self.confidence,
            "strategy": self.strategy,
            "evidence": self.evidence,
            "rationale": self.rationale,
        }


@dataclass
class LinkerResult:
    """Result of running ProjectLinker on a single record + its artifacts."""

    auto_links: list[MatchCandidate] = field(default_factory=list)
    suggestions: list[MatchCandidate] = field(default_factory=list)
    # Entities that had no match above threshold
    unmatched: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_links": [m.to_dict() for m in self.auto_links],
            "suggestions": [m.to_dict() for m in self.suggestions],
            "unmatched": self.unmatched,
        }
