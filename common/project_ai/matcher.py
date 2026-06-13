"""ProjectLinker: match records and artifacts to existing projects.

Strategy layers (cheapest first):
  1. Deterministic: exact title / alias / project-string / category overlap.
  2. LLM fallback: only for entities that deterministic pass couldn't resolve.

Action boundaries (from design doc §7.2):
  - confidence >= 0.85 → auto-link  (auto_links)
  - 0.55 <= conf < 0.85 → suggest   (suggestions)
  - < 0.55 → discard                (unmatched)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

from common.project_ai.types import (
    CONFIDENCE_AUTO_LINK,
    CONFIDENCE_SUGGEST,
    LinkerResult,
    MatchCandidate,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols for store interaction (avoid importing concrete stores)
# ---------------------------------------------------------------------------

class ProjectStore(Protocol):
    """Minimal store interface the linker needs."""

    def list_projects(self, **kwargs: Any) -> list[Any]: ...
    def list_project_aliases(self, project_id: str) -> list[Any]: ...
    def create_project_link(self, link: Any) -> None: ...
    def create_project_suggestion(self, suggestion: Any) -> None: ...


# ---------------------------------------------------------------------------
# Deterministic matching helpers
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    """Lowercase, strip, collapse whitespace."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _token_set(text: str) -> set[str]:
    """Split normalised text into tokens, drop very short ones."""
    return {t for t in _normalize(text).split() if len(t) >= 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union)


def _deterministic_match(
    *,
    record_content: str,
    record_summary: str,
    artifact_projects: list[str],
    projects: list[dict[str, Any]],
    aliases_by_project: dict[str, list[str]],
) -> list[MatchCandidate]:
    """Match record + artifacts against known projects using string overlap.

    Returns list of MatchCandidate with confidence set.
    """
    candidates: list[MatchCandidate] = []
    record_tokens = _token_set(f"{record_content} {record_summary}")

    for proj in projects:
        pid = proj["id"]
        ptitle = proj.get("title", "")
        pdesc = proj.get("description", "")

        # Collect all matchable strings for this project
        matchable = [ptitle, pdesc] + aliases_by_project.get(pid, [])

        best_score = 0.0
        best_evidence: list[dict[str, str]] = []
        best_rationale = ""

        # 1) Check if any artifact's project string matches project title/alias
        for ap in artifact_projects:
            ap_norm = _normalize(ap)
            if not ap_norm:
                continue
            for m in matchable:
                m_norm = _normalize(m)
                if not m_norm:
                    continue
                # Exact substring match → high confidence
                if ap_norm == m_norm or m_norm in ap_norm or ap_norm in m_norm:
                    score = 0.92
                    if score > best_score:
                        best_score = score
                        best_rationale = f"Artifact project '{ap}' matches '{m}'"
                        best_evidence = [{"entity_type": "artifact", "entity_id": ""}]

        # 2) Token overlap between record content and project title/aliases
        for m in matchable:
            m_tokens = _token_set(m)
            score = _jaccard(record_tokens, m_tokens)
            # Boost if title tokens are fully contained
            title_tokens = _token_set(ptitle)
            if title_tokens and title_tokens.issubset(record_tokens):
                score = max(score, 0.88)
            if score > best_score:
                best_score = score
                best_rationale = f"Record content overlaps with '{m}'"
                best_evidence = [{"entity_type": "record", "entity_id": ""}]

        if best_score >= CONFIDENCE_SUGGEST:
            candidates.append(MatchCandidate(
                project_id=pid,
                project_title=ptitle,
                confidence=round(best_score, 2),
                strategy="deterministic",
                evidence=best_evidence,
                rationale=best_rationale,
            ))

    return candidates


# ---------------------------------------------------------------------------
# LLM-based matching
# ---------------------------------------------------------------------------

async def _llm_match(
    *,
    record_content: str,
    record_summary: str,
    projects: list[dict[str, Any]],
    agent: Any,
) -> list[MatchCandidate]:
    """Use LLM to find project matches for a record.

    `agent` must expose `async def run_prompt(system, user) -> str`.
    """
    if not projects:
        return []

    from common.project_ai.prompts import PROJECT_LINKING_SYSTEM_PROMPT

    project_list = "\n".join(
        f"- id={p['id']}, title={p.get('title','')}, description={p.get('description','')}"
        for p in projects
    )
    user_msg = (
        f"Record summary: {record_summary}\n\n"
        f"Record content: {record_content[:1000]}\n\n"
        f"Existing projects:\n{project_list}\n\n"
        "Return JSON with matches array."
    )

    try:
        raw = await agent.run_prompt(PROJECT_LINKING_SYSTEM_PROMPT, user_msg)
        data = json.loads(raw)
    except Exception:
        logger.warning("LLM project linking failed", exc_info=True)
        return []

    candidates: list[MatchCandidate] = []
    for m in data.get("matches", []):
        conf = float(m.get("confidence", 0))
        if conf < CONFIDENCE_SUGGEST:
            continue
        candidates.append(MatchCandidate(
            project_id=m.get("project_id", ""),
            project_title=m.get("project_title", ""),
            confidence=round(conf, 2),
            strategy="llm",
            evidence=m.get("evidence", []),
            rationale=m.get("rationale", ""),
        ))
    return candidates


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def match_record(
    *,
    record_id: str,
    record_content: str,
    record_summary: str,
    artifact_projects: list[str],
    projects: list[dict[str, Any]],
    aliases_by_project: dict[str, list[str]],
    agent: Any | None = None,
) -> LinkerResult:
    """Run the full linking pipeline on one record.

    Parameters
    ----------
    record_id : str
    record_content : str  – raw record text
    record_summary : str  – LLM-generated summary
    artifact_projects : list[str]
        Wolo: project strings from todos/decisions/highlights/experiments.
        Solo: category strings from todos.
    projects : list[dict]
        Active projects as dicts (need at least id, title, description).
    aliases_by_project : dict[str, list[str]]
        project_id → list of alias strings.
    agent : optional
        Must expose `async run_prompt(system, user) -> str`.
        If None, only deterministic matching is performed.

    Returns
    -------
    LinkerResult with auto_links, suggestions, unmatched.
    """
    if not projects:
        return LinkerResult()

    # Layer 1: deterministic
    det_candidates = _deterministic_match(
        record_content=record_content,
        record_summary=record_summary,
        artifact_projects=artifact_projects,
        projects=projects,
        aliases_by_project=aliases_by_project,
    )

    # Deduplicate by project_id (keep highest confidence)
    best_by_project: dict[str, MatchCandidate] = {}
    for c in det_candidates:
        prev = best_by_project.get(c.project_id)
        if prev is None or c.confidence > prev.confidence:
            best_by_project[c.project_id] = c

    # Fill in record_id on evidence
    for c in best_by_project.values():
        for ev in c.evidence:
            if not ev.get("entity_id"):
                ev["entity_id"] = record_id

    # Layer 2: LLM fallback only if deterministic found nothing above threshold
    if not best_by_project and agent is not None:
        llm_candidates = await _llm_match(
            record_content=record_content,
            record_summary=record_summary,
            projects=projects,
            agent=agent,
        )
        for c in llm_candidates:
            for ev in c.evidence:
                if not ev.get("entity_id"):
                    ev["entity_id"] = record_id
            prev = best_by_project.get(c.project_id)
            if prev is None or c.confidence > prev.confidence:
                best_by_project[c.project_id] = c

    # Classify into auto_links vs suggestions vs unmatched
    result = LinkerResult()
    for c in best_by_project.values():
        if c.confidence >= CONFIDENCE_AUTO_LINK:
            result.auto_links.append(c)
        else:
            result.suggestions.append(c)

    return result
