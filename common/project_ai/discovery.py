"""Project Discovery: identify themes in recent records that could become projects.

LLM-based (two-phase RAG): local retrieval of candidate topics by tag frequency
and date distribution, then focused LLM evaluation per topic with only the
relevant records — not a full context dump.

The LLM receives rich context about existing projects (title, summary, keywords)
to make semantic deduplication decisions instead of relying on string similarity.

Output: list of ``create_project`` suggestions ready to be written to the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# Minimum confidence for LLM-discovered project candidates.
LLM_DISCOVERY_MIN_CONFIDENCE = 0.85

# How many days of records to scan for discovery
DISCOVERY_WINDOW_DAYS = 90


class _DiscoveryEvidence(BaseModel):
    entity_type: str
    entity_id: str


class _DiscoveryCandidate(BaseModel):
    title: str
    summary: str
    keywords: list[str] = Field(default_factory=list)
    rationale: str = ""
    evidence: list[_DiscoveryEvidence] = Field(default_factory=list)
    suggested_milestones: list[str] = Field(default_factory=list)
    confidence: float = Field(..., ge=0.0, le=1.0)
    suggestion_type: str = "create_project"


class _DiscoveryResponse(BaseModel):
    candidates: list[_DiscoveryCandidate] = Field(default_factory=list)


class DiscoveryStore(Protocol):
    """Minimal store interface for discovery."""

    def list_records(self, **kwargs: Any) -> list[Any]: ...
    def list_projects(self, **kwargs: Any) -> list[Any]: ...
    def list_project_aliases(self, project_id: str) -> list[Any]: ...
    def list_todos(self, **kwargs: Any) -> list[Any]: ...
    def list_project_suggestions(self, **kwargs: Any) -> list[Any]: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _existing_projects_context(store: DiscoveryStore) -> list[dict[str, Any]]:
    """Collect rich context about existing projects, suggestions, and aliases.

    Returns a list of dicts with title, description (as summary), tags (as keywords),
    and aliases for each existing project and pending suggestion.
    """
    projects: list[dict[str, Any]] = []

    for p in store.list_projects():
        d = p.to_dict() if hasattr(p, "to_dict") else p
        aliases = []
        for a in store.list_project_aliases(d.get("id", "")):
            ad = a.to_dict() if hasattr(a, "to_dict") else a
            alias = ad.get("alias", "").strip()
            if alias:
                aliases.append(alias)
        projects.append({
            "title": d.get("title", ""),
            "description": d.get("description", ""),
            "tags": d.get("tags", ""),
            "aliases": aliases,
        })

    try:
        for s in store.list_project_suggestions():
            sd = s.to_dict() if hasattr(s, "to_dict") else s
            title = sd.get("title", "").strip()
            if title:
                payload = {}
                try:
                    payload = json.loads(sd.get("proposed_payload_json", "{}"))
                except (json.JSONDecodeError, TypeError):
                    pass
                projects.append({
                    "title": title,
                    "description": payload.get("description", sd.get("rationale", "")),
                    "tags": "",
                    "keywords": payload.get("keywords", []),
                    "aliases": [],
                    "is_suggestion": True,
                })
    except Exception:
        pass

    return projects


def _build_existing_projects_text(projects: list[dict[str, Any]]) -> str:
    """Format existing project context for the LLM prompt."""
    if not projects:
        return "(no existing projects)"

    lines: list[str] = []
    for i, p in enumerate(projects, 1):
        title = p.get("title", "")
        desc = p.get("description", "")
        tags = p.get("tags", "")
        keywords = p.get("keywords", [])
        aliases = p.get("aliases", [])

        parts = [f"{i}. **{title}**"]
        if aliases:
            parts.append(f"   Aliases: {', '.join(aliases)}")
        if desc:
            parts.append(f"   Summary: {desc}")
        keyword_terms = keywords if keywords else (
            [t.strip() for t in tags.split(",") if t.strip()] if tags else []
        )
        if keyword_terms:
            parts.append(f"   Keywords: {', '.join(keyword_terms)}")
        if p.get("is_suggestion"):
            parts.append("   (pending suggestion, not yet accepted)")

        lines.append("\n".join(parts))

    return "\n\n".join(lines)


def _recent_record_summaries(store: DiscoveryStore, days: int = DISCOVERY_WINDOW_DAYS) -> list[dict[str, str]]:
    """Return list of {id, summary, tags} from recent records."""
    records = store.list_records(limit=500)
    result = []
    for r in records[:500]:
        d = r.to_dict() if hasattr(r, "to_dict") else r
        result.append({
            "id": d.get("id", ""),
            "summary": d.get("summary", ""),
            "tags": d.get("tags", ""),
            "date": d.get("date", ""),
        })
    return result


# ---------------------------------------------------------------------------
# LLM-based discovery (two-phase: local retrieval → focused LLM evaluation)
# ---------------------------------------------------------------------------

# Max concurrent LLM calls during topic evaluation
_MAX_CONCURRENT_EVALS = 5
# Max candidate topics to evaluate per scan
_MAX_TOPICS_TO_EVALUATE = 10
# Min occurrences for a tag to become a candidate topic
_TOPIC_MIN_OCCURRENCES = 5
# Min distinct dates for a tag to become a candidate topic
_TOPIC_MIN_DATES = 3


async def _llm_discover(
    *,
    records: list[dict[str, str]],
    artifact_projects: list[dict[str, str]],
    existing_projects_text: str,
    existing_titles: set[str],
    agent: Any,
) -> list[dict[str, Any]]:
    """Two-phase project discovery: local retrieval then focused LLM evaluation.

    Phase 1 (local): Identify candidate topics from tags/artifacts by frequency
    and date distribution — no LLM cost.
    Phase 2 (LLM): For each candidate, retrieve only the relevant records and
    send a focused evaluation prompt — each call processes ~15 records instead
    of 100+.
    """
    # Phase 1: Local retrieval — identify candidate topics
    topic_records: dict[str, list[dict[str, str]]] = {}
    topic_dates: dict[str, set[str]] = {}

    for rec in records:
        tags = rec.get("tags", "")
        rec_date = rec.get("date", "")
        for tag in tags.split(","):
            tag = tag.strip().lower()
            if len(tag) >= 2:
                topic_records.setdefault(tag, []).append(rec)
                topic_dates.setdefault(tag, set()).add(rec_date)

    # Also include artifact project strings as topics
    for ap in artifact_projects:
        proj = ap.get("project", "").strip().lower()
        if proj and len(proj) >= 2:
            topic_records.setdefault(proj, []).append({
                "id": ap.get("record_id", ""),
                "summary": f"[artifact] {proj}",
                "tags": proj,
                "date": ap.get("date", ""),
            })
            topic_dates.setdefault(proj, set()).add(ap.get("date", ""))

    # Select promising topics: enough occurrences, spanning multiple dates
    promising: list[tuple[str, int]] = []
    for topic, recs in topic_records.items():
        dates = topic_dates.get(topic, set())
        if len(recs) >= _TOPIC_MIN_OCCURRENCES and len(dates) >= _TOPIC_MIN_DATES:
            promising.append((topic, len(recs)))

    # Sort by record count descending, take top N
    promising.sort(key=lambda x: x[1], reverse=True)
    topics_to_eval = promising[:_MAX_TOPICS_TO_EVALUATE]

    if not topics_to_eval:
        return []

    # Phase 2: Focused LLM evaluation — one call per topic
    sem = asyncio.Semaphore(_MAX_CONCURRENT_EVALS)

    async def _bounded_eval(topic: str, topic_recs: list[dict[str, str]]) -> dict[str, Any] | None:
        async with sem:
            return await _evaluate_topic(
                topic=topic,
                topic_records=topic_recs[:15],
                existing_projects_text=existing_projects_text,
                agent=agent,
            )

    eval_results = await asyncio.gather(*[
        _bounded_eval(topic, topic_records[topic])
        for topic, _ in topics_to_eval
    ])

    # Collect valid candidates; exact-title dedup as safety net (semantic dedup is LLM's job)
    accepted_titles: list[str] = [t.lower() for t in existing_titles]
    candidates: list[dict[str, Any]] = []
    for result in eval_results:
        if not result:
            continue
        conf = float(result.get("confidence", 0))
        if conf < LLM_DISCOVERY_MIN_CONFIDENCE:
            continue
        title = result.get("title", "").strip()
        title_lower = title.lower()
        if title_lower in accepted_titles:
            continue
        accepted_titles.append(title_lower)
        candidates.append({
            "title": title,
            "summary": result.get("summary", ""),
            "keywords": result.get("keywords", []),
            "rationale": result.get("rationale", ""),
            "evidence": result.get("evidence", []),
            "suggested_milestones": result.get("suggested_milestones", []),
            "confidence": round(conf, 2),
            "suggestion_type": "create_project",
        })

    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:3]


async def _evaluate_topic(
    *,
    topic: str,
    topic_records: list[dict[str, str]],
    existing_projects_text: str,
    agent: Any,
) -> dict[str, Any] | None:
    """Evaluate a single candidate topic with focused records.

    Sends only the records relevant to this topic (typically 10-15)
    instead of the full record set. This is the 'R' in RAG — retrieve
    then generate.
    """
    from common.project_ai.prompts import (
        EXISTING_PROJECTS_CONTEXT_PROMPT,
        PROJECT_DISCOVERY_SYSTEM_PROMPT,
    )

    records_text = "\n".join(
        f"- [{r.get('date', '')}] {r.get('summary', '')} (tags: {r.get('tags', '')})"
        for r in topic_records
    )

    existing_block = EXISTING_PROJECTS_CONTEXT_PROMPT.format(
        existing_projects_context=existing_projects_text,
    )

    user_msg = (
        f"## Topic: \"{topic}\"\n\n"
        f"## Related records ({len(topic_records)} records)\n{records_text}\n\n"
        f"{existing_block}\n\n"
        "Determine if the above records reveal a genuine, ongoing project or goal "
        "related to this topic. Apply strict criteria: look for explicit goal "
        "statements, commitments, or sustained tracking behavior across multiple "
        "dates. Returning 0 candidates (empty array) is the correct answer if "
        "the evidence is weak. "
        "CRITICAL: Before suggesting any candidate, verify it is NOT the same "
        "endeavor as any existing project listed above. If there is any overlap "
        "in meaning or scope, return an empty candidates array. Return JSON."
    )

    try:
        raw = await agent.run_prompt(PROJECT_DISCOVERY_SYSTEM_PROMPT, user_msg)
        data = _DiscoveryResponse.model_validate_json(raw)
    except Exception:
        logger.warning("LLM topic evaluation failed for '%s'", topic, exc_info=True)
        return None

    if not data.candidates:
        return None

    return data.candidates[0].model_dump()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def scan_for_projects(
    *,
    store: DiscoveryStore,
    agent: Any | None = None,
    days: int = DISCOVERY_WINDOW_DAYS,
) -> list[dict[str, Any]]:
    """Scan recent records and artifacts for new project candidates.

    Parameters
    ----------
    store : DiscoveryStore
        Must expose list_records, list_projects, list_project_aliases, list_todos.
    agent : required
        Must expose ``async run_prompt(system, user) -> str``.
        If None, raises RuntimeError — discovery requires LLM capabilities.
    days : int
        How many days of records to scan.

    Returns
    -------
    list[dict]
        Each dict has: title, summary, keywords, rationale, evidence,
        suggested_milestones, confidence, suggestion_type.
    """
    if agent is None:
        raise RuntimeError(
            "Project discovery requires an AI agent. "
            "Please check your model configuration and try again."
        )

    existing_projects = _existing_projects_context(store)
    existing_projects_text = _build_existing_projects_text(existing_projects)
    existing_titles = {p["title"].lower().strip() for p in existing_projects if p.get("title")}

    records = _recent_record_summaries(store, days)

    if not records:
        return []

    # Collect artifact project strings
    artifact_projects: list[dict[str, str]] = []
    try:
        todos = store.list_todos(status="pending", limit=100)
        for t in todos:
            d = t.to_dict() if hasattr(t, "to_dict") else t
            proj = d.get("project") or d.get("category") or ""
            if proj:
                artifact_projects.append({
                    "project": str(proj),
                    "type": "todo",
                    "record_id": d.get("record_id", ""),
                })
    except Exception:
        pass

    candidates = await _llm_discover(
        records=records,
        artifact_projects=artifact_projects,
        existing_projects_text=existing_projects_text,
        existing_titles=existing_titles,
        agent=agent,
    )

    return candidates
