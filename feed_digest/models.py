"""Domain models for the feed_digest module."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class FeedItem:
    """Normalized feed item from any external source."""

    source: str
    title: str
    url: str
    content: str
    published_at: str
    author: str = ""
    domain: str = ""
    preset: str = ""
    score: float = 0.0
    summary: str = ""
    tags: list[str] = field(default_factory=list)
    key_facts: list[str] = field(default_factory=list)
    importance_reason: str = ""
    cluster_id: str = ""
    cluster_title: str = ""
    duplicate_of: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceStats:
    source: str
    fetched: int = 0
    selected: int = 0
    failed: bool = False
    warning: str = ""


@dataclass
class FeedDigestResult:
    """Result of one feed digest run."""

    date: str
    domain: str
    preset: str
    period_start: str
    period_end: str
    items: list[FeedItem] = field(default_factory=list)
    selected_items: list[FeedItem] = field(default_factory=list)
    trends: list[str] = field(default_factory=list)
    overview: str = ""
    markdown: str = ""
    source_stats: list[SourceStats] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    is_empty: bool = False


@runtime_checkable
class FeedSource(Protocol):
    """Protocol that all feed sources must implement."""

    async def collect(
        self,
        *,
        since: str,
        until: str,
        domain: str,
        query: str,
        max_items: int,
    ) -> list[FeedItem]: ...
