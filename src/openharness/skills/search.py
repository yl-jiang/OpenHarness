"""Skill search: BM25 + heuristic hybrid ranking for progressive skill discovery.

Powers ``skill_search(query=...)`` so the agent can find the most relevant
skill without scanning the full list.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from openharness.search.text import (
    compute_rrf_scores,
    tokenize_for_bm25,
    tokenize_heuristic,
)

if TYPE_CHECKING:
    from openharness.skills.registry import SkillRegistry
    from openharness.skills.types import SkillDefinition

try:
    from rank_bm25 import BM25Okapi  # type: ignore

    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


_BODY_PREVIEW_CHARS = 800
_NAME_EXACT_BONUS = 2.0
_TAG_MATCH_BONUS = 1.0
_TAG_MATCH_CAP = 4.0
_METADATA_WEIGHT = 2.0
_BODY_WEIGHT = 1.0


@dataclass(frozen=True)
class SkillSearchResult:
    """One ranked skill returned by :func:`find_relevant_skills`."""

    skill: SkillDefinition
    score: float


# =========================================================================
# Corpus preparation
# =========================================================================


def _skill_metadata_text(skill: SkillDefinition) -> str:
    """Compose the searchable metadata string for a skill."""
    tags = " ".join(skill.tags)
    return f"{skill.name} {tags} {skill.description}".strip()


def _skill_body_text(skill: SkillDefinition) -> str:
    """Return a bounded body preview (frontmatter stripped)."""
    content = skill.content or ""
    body = content
    if content.startswith("---\n"):
        end_marker = content.find("\n---\n", 4)
        if end_marker != -1:
            body = content[end_marker + 5:]
    return body[:_BODY_PREVIEW_CHARS]


# =========================================================================
# BM25 cache
# =========================================================================


@dataclass
class _SkillBM25Index:
    meta_okapi: object | None
    body_okapi: object | None
    signatures: dict[str, str]


class _SkillBM25Cache:
    """Cache BM25 indices across calls; invalidate when skills change."""

    def __init__(self) -> None:
        self._cache: _SkillBM25Index | None = None

    @staticmethod
    def _signature(skill: SkillDefinition) -> str:
        if skill.path:
            try:
                return f"mtime:{Path(skill.path).stat().st_mtime_ns}"
            except OSError:
                pass
        return f"hash:{hashlib.sha256(skill.content.encode('utf-8')).hexdigest()}"

    def _signatures(self, skills: list[SkillDefinition]) -> dict[str, str]:
        return {skill.name: self._signature(skill) for skill in skills}

    def get_or_build(self, skills: list[SkillDefinition]) -> _SkillBM25Index | None:
        if not _HAS_BM25:
            return None

        signatures = self._signatures(skills)
        if self._cache is not None and self._cache.signatures == signatures:
            return self._cache

        meta_corpus = [tokenize_for_bm25(_skill_metadata_text(s)) for s in skills]
        body_corpus = [tokenize_for_bm25(_skill_body_text(s)) for s in skills]

        meta_okapi = BM25Okapi(meta_corpus) if any(meta_corpus) else None
        body_okapi = BM25Okapi(body_corpus) if any(body_corpus) else None

        self._cache = _SkillBM25Index(
            meta_okapi=meta_okapi,
            body_okapi=body_okapi,
            signatures=signatures,
        )
        return self._cache


_global_skill_bm25_cache = _SkillBM25Cache()


def _get_bm25_scores(
    query: str,
    skills: list[SkillDefinition],
) -> tuple[list[float], list[float]]:
    n = len(skills)
    if not _HAS_BM25 or n == 0:
        return [0.0] * n, [0.0] * n

    index = _global_skill_bm25_cache.get_or_build(skills)
    if index is None:
        return [0.0] * n, [0.0] * n

    tokenized = tokenize_for_bm25(query)
    meta_scores = (
        list(index.meta_okapi.get_scores(tokenized))  # type: ignore[union-attr]
        if index.meta_okapi
        else [0.0] * n
    )
    body_scores = (
        list(index.body_okapi.get_scores(tokenized))  # type: ignore[union-attr]
        if index.body_okapi
        else [0.0] * n
    )
    return meta_scores, body_scores


# =========================================================================
# Heuristic scoring
# =========================================================================


def _get_heuristic_scores(
    query: str,
    skills: list[SkillDefinition],
) -> list[float]:
    """Token-frequency + field bonuses."""
    tokens = tokenize_heuristic(query)
    query_lower = query.lower()
    if not tokens:
        return [0.0] * len(skills)

    scores: list[float] = []
    for skill in skills:
        meta = _skill_metadata_text(skill).lower()
        body = _skill_body_text(skill).lower()
        meta_hits = sum(1 for t in tokens if t in meta)
        body_hits = sum(1 for t in tokens if t in body)
        score = float(meta_hits * _METADATA_WEIGHT + body_hits * _BODY_WEIGHT)

        if skill.name.lower() == query_lower:
            score += _NAME_EXACT_BONUS

        if skill.tags and tokens:
            tag_hits = sum(
                1 for tag in skill.tags for t in tokens if t in tag.lower()
            )
            score += min(float(tag_hits) * _TAG_MATCH_BONUS, _TAG_MATCH_CAP)

        scores.append(score)
    return scores


# =========================================================================
# Public entry
# =========================================================================


def find_relevant_skills(
    query: str,
    registry: SkillRegistry,
    *,
    max_results: int = 8,
    tag_filter: str | None = None,
) -> list[SkillSearchResult]:
    """Return the skills most relevant to *query*, ranked by hybrid score.

    Scoring fuses BM25 with a token-frequency heuristic via Reciprocal Rank
    Fusion, then applies field bonuses (name / tag exact match). Results are
    sorted by descending score and truncated to *max_results*.
    """
    if not query or not query.strip():
        return []

    skills = list(registry.list_skills())

    if tag_filter:
        tag_lower = tag_filter.lower()
        skills = [
            s for s in skills
            if any(t.lower() == tag_lower for t in s.tags)
        ]

    if not skills:
        return []

    meta_bm25, body_bm25 = _get_bm25_scores(query, skills)
    bm25_combined = [
        meta * _METADATA_WEIGHT + body * _BODY_WEIGHT
        for meta, body in zip(meta_bm25, body_bm25)
    ]

    heuristic_combined = _get_heuristic_scores(query, skills)

    fused = compute_rrf_scores(bm25_combined, heuristic_combined)

    scored: list[tuple[float, SkillDefinition]] = []
    for i, skill in enumerate(skills):
        base = fused[i] if i < len(fused) else 0.0
        if bm25_combined[i] > 0 or heuristic_combined[i] > 0 or base > 0:
            scored.append((base, skill))

    scored.sort(key=lambda item: (-item[0], item[1].name))

    limit = max(1, max_results)
    return [
        SkillSearchResult(skill=skill, score=score)
        for score, skill in scored[:limit]
    ]


__all__ = ["SkillSearchResult", "find_relevant_skills"]
