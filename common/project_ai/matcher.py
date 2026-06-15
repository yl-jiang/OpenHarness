"""ProjectLinker: match records and artifacts to existing projects.

Strategy layers (cheapest first):
  1. Deterministic: exact title / alias / project-string / category overlap.
  2. LLM: semantic matching, always runs when agent is available.

Fusion: both layers run independently; results are merged per project_id.
  - Same project found by both → keep higher confidence, strategy="hybrid".
  - Found by only one layer → keep as-is with original strategy.

Action boundaries (from design doc §7.2):
  - confidence >= 0.85 → auto-link  (auto_links)
  - 0.55 <= conf < 0.85 → suggest   (suggestions)
  - < 0.55 → discard                (unmatched)
"""

from __future__ import annotations

import logging
import re
from typing import Any, Protocol

from common.project_ai.types import (
    CONFIDENCE_AUTO_LINK,
    CONFIDENCE_SUGGEST,
    LinkerResult,
    LlmMatchItem,
    LlmMatchResponse,
    MatchCandidate,
    ProjectLinkInput,
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


def tokenize_enhanced(text: str) -> list[str]:
    """Tokenize text: Jieba for Chinese, regex for English/numbers."""
    if not text:
        return []

    text = text.lower()

    if not re.search(r"[\u4e00-\u9fff]", text):
        return re.findall(r"[a-z0-9]{2,}", text)

    import jieba

    tokens = [t.strip() for t in jieba.cut(text) if t.strip()]
    ascii_tokens = re.findall(r"[a-z0-9]{2,}", text)
    return list(dict.fromkeys(tokens + ascii_tokens))


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
    projects: list[ProjectLinkInput],
    aliases_by_project: dict[str, list[str]],
) -> list[MatchCandidate]:
    """Match record + artifacts against known projects using string overlap.

    Returns list of MatchCandidate with confidence set.
    """
    candidates: list[MatchCandidate] = []
    record_tokens = set(tokenize_enhanced(f"{record_content} {record_summary}"))

    for proj in projects:
        pid = proj.id
        ptitle = proj.title
        pdesc = proj.description

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
            m_tokens = set(tokenize_enhanced(m))
            score = _jaccard(record_tokens, m_tokens)
            title_tokens = set(tokenize_enhanced(ptitle))
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

def _validate_llm_matches(
    matches: list[LlmMatchItem],
    projects: list[ProjectLinkInput],
) -> tuple[list[MatchCandidate], list[str]]:
    """Validate LLM-returned matches against known projects.

    Prevents hallucination by:
    - Rejecting unknown project_ids
    - Correcting mismatched project_titles for valid IDs
    - Replacing unknown evidence entity_types with "record"

    Returns (candidates, errors) where errors is a list of human-readable
    validation problems that can be fed back to the LLM for retry.
    """
    valid_project_ids = {p.id for p in projects}
    project_titles = {p.id: p.title for p in projects}
    valid_entity_types = {"record", "artifact"}

    candidates: list[MatchCandidate] = []
    errors: list[str] = []

    for m in matches:
        if m.confidence < CONFIDENCE_SUGGEST:
            continue

        # Reject hallucinated project_id
        if m.project_id not in valid_project_ids:
            err = (
                f"project_id='{m.project_id}' does not exist. "
                f"Valid project IDs: {sorted(valid_project_ids)}."
            )
            errors.append(err)
            logger.warning(
                "LLM returned unknown project_id='%s' (title='%s'), skipping. "
                "Valid IDs: %s",
                m.project_id, m.project_title, valid_project_ids,
            )
            continue

        # Fix project_title if LLM hallucinated a wrong title for a valid ID
        expected_title = project_titles.get(m.project_id, "")
        if expected_title and m.project_title and m.project_title != expected_title:
            errors.append(
                f"project_id='{m.project_id}' has title='{expected_title}', "
                f"not '{m.project_title}'."
            )
            logger.info(
                "LLM returned mismatched title for project_id='%s': "
                "got='%s', expected='%s'; using expected title",
                m.project_id, m.project_title, expected_title,
            )

        # Validate evidence entity_type against known types
        for ev in m.evidence:
            if ev.entity_type not in valid_entity_types:
                errors.append(
                    f"entity_type='{ev.entity_type}' is invalid for "
                    f"project_id='{m.project_id}'. "
                    f"Valid types: {sorted(valid_entity_types)}."
                )
                logger.warning(
                    "LLM returned unknown entity_type='%s' in evidence for "
                    "project_id='%s', replacing with 'record'",
                    ev.entity_type, m.project_id,
                )
                ev.entity_type = "record"

        candidates.append(MatchCandidate(
            project_id=m.project_id,
            project_title=expected_title or m.project_title,
            confidence=round(m.confidence, 2),
            strategy="llm",
            evidence=[e.model_dump() for e in m.evidence],
            rationale=m.rationale,
        ))
    return candidates, errors


async def _llm_match(
    *,
    record_content: str,
    record_summary: str,
    projects: list[ProjectLinkInput],
    agent: Any,
    domain: str = "wolo",
    exclude_project_ids: set[str] | None = None,
) -> list[MatchCandidate]:
    """Use LLM to find project matches for a record.

    `agent` must expose `async def run_prompt(system, user) -> str`.

    `exclude_project_ids` are projects already matched by deterministic
    layer; LLM is asked to focus on the remaining projects but may still
    return them (they will be merged as hybrid in the caller).
    """
    if not projects:
        return []

    from common.project_ai.prompts import PROJECT_LINKING_SYSTEM_PROMPT

    # Filter projects: prefer unresolved ones, but keep all for LLM context
    target_projects = projects
    if exclude_project_ids:
        target_projects = [p for p in projects if p.id not in exclude_project_ids]
        # If all projects are already matched, still let LLM evaluate all
        if not target_projects:
            target_projects = projects

    project_list = "\n".join(
        f"- id={p.id}, title={p.title}, description={p.description}"
        for p in target_projects
    )
    domain_hints = {
        "wolo": "Domain: work journal. Records describe work activities, project progress, strategies, deadlines. Projects are work endeavors with stakeholders and milestones. Artifact project fields reference work project names.",
        "solo": "Domain: personal reflection. Records describe personal experiences, friction signals, awareness moments, behavioral experiments. Projects are personal growth endeavors like habit formation, self-experiments, life improvements. Artifact category fields reference life areas.",
    }
    domain_context = domain_hints.get(domain, "")
    user_msg = (
        f"{domain_context}\n\n"
        f"Record summary: {record_summary}\n\n"
        f"Record content: {record_content[:1000]}\n\n"
        f"Existing projects:\n{project_list}\n\n"
        "Return JSON with matches array."
    )

    max_retries = 3
    feedback: list[str] = []
    candidates: list[MatchCandidate] = []

    for attempt in range(1, max_retries + 1):
        attempt_msg = user_msg
        if feedback:
            attempt_msg += (
                "\n\n---\nPrevious attempt had errors, please fix:\n"
                + "\n".join(f"- {e}" for e in feedback)
            )

        try:
            raw = await agent.run_prompt(PROJECT_LINKING_SYSTEM_PROMPT, attempt_msg)
            parsed = LlmMatchResponse.model_validate_json(raw)
        except Exception:
            logger.warning(
                "LLM project linking failed (attempt %d/%d)",
                attempt, max_retries, exc_info=True,
            )
            feedback = ["Failed to parse JSON response. Return valid JSON only."]
            continue

        candidates, errors = _validate_llm_matches(parsed.matches, projects)
        if not errors:
            return candidates

        logger.info(
            "LLM match validation errors (attempt %d/%d): %s",
            attempt, max_retries, errors,
        )
        feedback = errors

    # All retries exhausted; return last valid candidates (with corrections applied)
    logger.warning("LLM match retries exhausted after %d attempts", max_retries)
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
    projects: list[ProjectLinkInput],
    aliases_by_project: dict[str, list[str]],
    agent: Any | None = None,
    domain: str = "wolo",
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
    projects : list[ProjectLinkInput]
        Active projects as ProjectLinkInput models.
    aliases_by_project : dict[str, list[str]]
        project_id → list of alias strings.
    agent : optional
        Must expose `async run_prompt(system, user) -> str`.
        If None, only deterministic matching is performed.
    domain : str
        "wolo" for work journal or "solo" for personal reflection.
        Affects LLM prompt context to improve matching accuracy.

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


    # Deduplicate deterministic results by project_id (keep highest confidence)
    best_by_project: dict[str, MatchCandidate] = {}
    for c in det_candidates:
        prev = best_by_project.get(c.project_id)
        if prev is None or c.confidence > prev.confidence:
            best_by_project[c.project_id] = c

    # Fill in record_id on deterministic evidence
    for c in best_by_project.values():
        for ev in c.evidence:
            if not ev.get("entity_id"):
                ev["entity_id"] = record_id

    # Layer 2: LLM always runs when agent is available (fusion, not fallback)
    if agent is not None:
        # Pass deterministic matches so LLM can focus on unresolved projects
        det_project_ids = set(best_by_project.keys())
        llm_candidates = await _llm_match(
            record_content=record_content,
            record_summary=record_summary,
            projects=projects,
            agent=agent,
            domain=domain,
            exclude_project_ids=det_project_ids,
        )
        for c in llm_candidates:
            for ev in c.evidence:
                if not ev.get("entity_id"):
                    ev["entity_id"] = record_id
            prev = best_by_project.get(c.project_id)
            if prev is not None:
                # Both layers matched same project → hybrid with max confidence
                merged_conf = max(prev.confidence, c.confidence)
                best_by_project[c.project_id] = MatchCandidate(
                    project_id=c.project_id,
                    project_title=c.project_title,
                    confidence=round(merged_conf, 2),
                    strategy="hybrid",
                    evidence=prev.evidence + c.evidence,
                    rationale=f"[det] {prev.rationale} | [llm] {c.rationale}",
                )
            else:
                best_by_project[c.project_id] = c

    # Classify into auto_links vs suggestions vs unmatched
    result = LinkerResult()
    for c in best_by_project.values():
        if c.confidence >= CONFIDENCE_AUTO_LINK:
            result.auto_links.append(c)
        else:
            result.suggestions.append(c)

    return result
