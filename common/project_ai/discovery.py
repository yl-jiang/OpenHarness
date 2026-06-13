"""Project Discovery: identify themes in recent records that could become projects.

Two strategies:
  1. LLM-based (two-phase RAG): local retrieval of candidate topics by tag
     frequency and date distribution, then focused LLM evaluation per topic
     with only the relevant records — not a full context dump.
  2. Deterministic fallback: count recurring project/category strings from artifacts.

Output: list of ``create_project`` suggestions ready to be written to the store.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from typing import Any, Protocol

from common.project_ai.types import CONFIDENCE_SUGGEST

logger = logging.getLogger(__name__)

# Minimum confidence for LLM-discovered project candidates (stricter than the
# shared CONFIDENCE_SUGGEST used for record-linking).
LLM_DISCOVERY_MIN_CONFIDENCE = 0.70

# How many days of records to scan for discovery
DISCOVERY_WINDOW_DAYS = 90


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

def _existing_titles_and_aliases(store: DiscoveryStore) -> set[str]:
    """Collect all lowercased project titles, aliases, and pending suggestion titles."""
    names: set[str] = set()
    for p in store.list_projects():
        d = p.to_dict() if hasattr(p, "to_dict") else p
        names.add(d.get("title", "").lower().strip())
        for a in store.list_project_aliases(d.get("id", "")):
            ad = a.to_dict() if hasattr(a, "to_dict") else a
            alias = ad.get("alias", "").lower().strip()
            if alias:
                names.add(alias)
    # Also exclude titles that already have a pending suggestion
    try:
        for s in store.list_project_suggestions(status="pending"):
            sd = s.to_dict() if hasattr(s, "to_dict") else s
            title = sd.get("title", "").lower().strip()
            if title:
                names.add(title)
    except Exception:
        pass
    return names


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


def _deterministic_discover(
    *,
    records: list[dict[str, str]],
    artifact_projects: list[dict[str, str]],
    existing_names: set[str],
) -> list[dict[str, Any]]:
    """Conservative frequency-based discovery without LLM.

    Counts recurring ``project`` strings from artifacts and ``tags`` from records.
    Uses a high occurrence threshold and requires cross-date evidence to reduce noise.
    This is a last-resort fallback; the LLM path is the primary discovery mechanism.
    Returns candidate dicts matching the discovery output schema.
    """
    tag_counter: Counter[str] = Counter()
    tag_records: dict[str, list[str]] = {}  # tag → record ids
    tag_dates: dict[str, set[str]] = {}  # tag → set of distinct dates

    for rec in records:
        tags = rec.get("tags", "")
        rec_date = rec.get("date", "")
        for t in tags.split(","):
            t = t.strip().lower()
            if len(t) >= 2:
                tag_counter[t] += 1
                tag_records.setdefault(t, []).append(rec["id"])
                tag_dates.setdefault(t, set()).add(rec_date)

    proj_counter: Counter[str] = Counter()
    proj_records: dict[str, list[str]] = {}
    proj_dates: dict[str, set[str]] = {}

    for ap in artifact_projects:
        name = ap.get("project", "").strip().lower()
        if name and len(name) >= 2:
            proj_counter[name] += 1
            proj_records.setdefault(name, []).append(ap.get("record_id", ""))
            proj_dates.setdefault(name, set()).add(ap.get("date", ""))

    candidates: list[dict[str, Any]] = []

    # Merge tag and project string counts
    all_names = set(tag_counter.keys()) | set(proj_counter.keys())
    for name in all_names:
        count = tag_counter.get(name, 0) + proj_counter.get(name, 0)
        if count < 15:
            continue  # very high threshold for LLM-less fallback (no semantic filtering)
        if name in existing_names:
            continue  # already a known project

        # Require evidence across at least 3 distinct dates
        dates = tag_dates.get(name, set()) | proj_dates.get(name, set())
        if len(dates) < 3:
            continue

        evidence_ids = list(set(tag_records.get(name, []) + proj_records.get(name, [])))
        confidence = min(0.70 + (count - 15) * 0.01, 0.85)

        candidates.append({
            "title": name.title(),
            "rationale": f"'{name}' appears {count} times across {len(dates)} distinct days",
            "evidence": [
                {"entity_type": "record", "entity_id": eid}
                for eid in evidence_ids[:5]
            ],
            "suggested_milestones": [],
            "confidence": round(confidence, 2),
            "suggestion_type": "create_project",
        })

    # Sort by confidence descending
    candidates.sort(key=lambda c: c["confidence"], reverse=True)
    return candidates[:3]


# ---------------------------------------------------------------------------
# LLM-based discovery (two-phase: local retrieval → focused LLM evaluation)
# ---------------------------------------------------------------------------

# Max concurrent LLM calls during topic evaluation
_MAX_CONCURRENT_EVALS = 5
# Max candidate topics to evaluate per scan
_MAX_TOPICS_TO_EVALUATE = 10
# Min occurrences for a tag to become a candidate topic
_TOPIC_MIN_OCCURRENCES = 3
# Min distinct dates for a tag to become a candidate topic
_TOPIC_MIN_DATES = 2


async def _llm_discover(
    *,
    records: list[dict[str, str]],
    artifact_projects: list[dict[str, str]],
    existing_names: set[str],
    agent: Any,
) -> list[dict[str, Any]]:
    """Two-phase project discovery: local retrieval then focused LLM evaluation.

    Phase 1 (local): Identify candidate topics from tags/artifacts by frequency
    and date distribution — no LLM cost.
    Phase 2 (LLM): For each candidate, retrieve only the relevant records and
    send a focused evaluation prompt — each call processes ~15 records instead
    of 100+.
    """
    existing_text = ", ".join(sorted(existing_names)) if existing_names else "(none)"

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
                existing_text=existing_text,
                agent=agent,
            )

    eval_results = await asyncio.gather(*[
        _bounded_eval(topic, topic_records[topic])
        for topic, _ in topics_to_eval
    ])

    # Collect valid candidates, deduplicating by title
    seen_titles: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for result in eval_results:
        if not result:
            continue
        conf = float(result.get("confidence", 0))
        if conf < LLM_DISCOVERY_MIN_CONFIDENCE:
            continue
        title_lower = result.get("title", "").lower().strip()
        if title_lower in existing_names or title_lower in seen_titles:
            continue
        seen_titles.add(title_lower)
        candidates.append({
            "title": result.get("title", ""),
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
    existing_text: str,
    agent: Any,
) -> dict[str, Any] | None:
    """Evaluate a single candidate topic with focused records.

    Sends only the records relevant to this topic (typically 10-15)
    instead of the full record set. This is the 'R' in RAG — retrieve
    then generate.
    """
    from common.project_ai.prompts import PROJECT_DISCOVERY_SYSTEM_PROMPT

    records_text = "\n".join(
        f"- [{r.get('date', '')}] {r.get('summary', '')} (tags: {r.get('tags', '')})"
        for r in topic_records
    )

    user_msg = (
        f"## Topic: \"{topic}\"\n\n"
        f"## Related records ({len(topic_records)} records)\n{records_text}\n\n"
        f"## Existing projects & aliases\n{existing_text}\n\n"
        "Determine if the above records reveal a genuine, ongoing project or goal "
        "related to this topic. Apply strict criteria: look for explicit goal "
        "statements, commitments, or sustained tracking behavior across multiple "
        "dates. Returning 0 candidates (empty array) is the correct answer if "
        "the evidence is weak. Return JSON."
    )

    try:
        raw = await agent.run_prompt(PROJECT_DISCOVERY_SYSTEM_PROMPT, user_msg)
        data = json.loads(raw)
    except Exception:
        logger.warning("LLM topic evaluation failed for '%s'", topic, exc_info=True)
        return None

    evaluated = data.get("candidates", [])
    if not evaluated:
        return None

    return evaluated[0]


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
    agent : optional
        Must expose ``async run_prompt(system, user) -> str``.
        If None, uses deterministic frequency-based discovery.
    days : int
        How many days of records to scan.

    Returns
    -------
    list[dict]
        Each dict has: title, rationale, evidence, suggested_milestones,
        confidence, suggestion_type.
    """
    existing_names = _existing_titles_and_aliases(store)
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

    if agent is not None:
        candidates = await _llm_discover(
            records=records,
            artifact_projects=artifact_projects,
            existing_names=existing_names,
            agent=agent,
        )
        # If LLM returned nothing, fall back to deterministic
        if not candidates:
            candidates = _deterministic_discover(
                records=records,
                artifact_projects=artifact_projects,
                existing_names=existing_names,
            )
    else:
        candidates = _deterministic_discover(
            records=records,
            artifact_projects=artifact_projects,
            existing_names=existing_names,
        )

    return candidates
