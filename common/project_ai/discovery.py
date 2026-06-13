"""Project Discovery: identify themes in recent records that could become projects.

Two strategies:
  1. LLM-based (when agent is available): semantic clustering and thematic analysis.
  2. Deterministic fallback: count recurring project/category strings from artifacts.

Output: list of ``create_project`` suggestions ready to be written to the store.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from typing import Any, Protocol

from common.project_ai.types import CONFIDENCE_SUGGEST

logger = logging.getLogger(__name__)

# How many days of records to scan for discovery
DISCOVERY_WINDOW_DAYS = 30


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
    records = store.list_records(limit=200)
    result = []
    for r in records[:200]:
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
    """Simple frequency-based discovery without LLM.

    Counts recurring ``project`` strings from artifacts and ``tags`` from records.
    Returns candidate dicts matching the discovery output schema.
    """
    tag_counter: Counter[str] = Counter()
    tag_records: dict[str, list[str]] = {}  # tag → record ids

    for rec in records:
        tags = rec.get("tags", "")
        for t in tags.split(","):
            t = t.strip().lower()
            if len(t) >= 2:
                tag_counter[t] += 1
                tag_records.setdefault(t, []).append(rec["id"])

    proj_counter: Counter[str] = Counter()
    proj_records: dict[str, list[str]] = {}

    for ap in artifact_projects:
        name = ap.get("project", "").strip().lower()
        if name and len(name) >= 2:
            proj_counter[name] += 1
            proj_records.setdefault(name, []).append(ap.get("record_id", ""))

    candidates: list[dict[str, Any]] = []

    # Merge tag and project string counts
    all_names = set(tag_counter.keys()) | set(proj_counter.keys())
    for name in all_names:
        count = tag_counter.get(name, 0) + proj_counter.get(name, 0)
        if count < 3:
            continue  # need at least 3 occurrences
        if name in existing_names:
            continue  # already a known project

        evidence_ids = list(set(tag_records.get(name, []) + proj_records.get(name, [])))
        confidence = min(0.55 + count * 0.05, 0.85)

        candidates.append({
            "title": name.title(),
            "rationale": f"'{name}' appears {count} times across recent records and artifacts",
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
    return candidates[:5]


# ---------------------------------------------------------------------------
# LLM-based discovery
# ---------------------------------------------------------------------------

async def _llm_discover(
    *,
    records: list[dict[str, str]],
    artifact_projects: list[dict[str, str]],
    existing_names: set[str],
    agent: Any,
) -> list[dict[str, Any]]:
    """Use LLM to analyze recent records for project themes."""
    from common.project_ai.prompts import PROJECT_DISCOVERY_SYSTEM_PROMPT

    # Build compact record summary for the prompt
    records_text = "\n".join(
        f"- [{r.get('date', '')}] {r.get('summary', '')} (tags: {r.get('tags', '')})"
        for r in records[:50]
    )
    artifacts_text = "\n".join(
        f"- {ap.get('project', '')} ({ap.get('type', 'todo')})"
        for ap in artifact_projects[:30]
    )
    existing_text = ", ".join(sorted(existing_names)) if existing_names else "(none)"

    user_msg = (
        f"## Recent records (last 30 days, up to 50)\n{records_text}\n\n"
        f"## Artifact project strings\n{artifacts_text}\n\n"
        f"## Existing projects & aliases\n{existing_text}\n\n"
        "Identify new project candidates. Return JSON."
    )

    try:
        raw = await agent.run_prompt(PROJECT_DISCOVERY_SYSTEM_PROMPT, user_msg)
        data = json.loads(raw)
    except Exception:
        logger.warning("LLM project discovery failed", exc_info=True)
        return []

    candidates: list[dict[str, Any]] = []
    for c in data.get("candidates", []):
        conf = float(c.get("confidence", 0))
        if conf < CONFIDENCE_SUGGEST:
            continue
        # Skip if title too close to existing
        title_lower = c.get("title", "").lower().strip()
        if title_lower in existing_names:
            continue
        candidates.append({
            "title": c.get("title", ""),
            "rationale": c.get("rationale", ""),
            "evidence": c.get("evidence", []),
            "suggested_milestones": c.get("suggested_milestones", []),
            "confidence": round(conf, 2),
            "suggestion_type": "create_project",
        })

    return candidates[:5]


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
