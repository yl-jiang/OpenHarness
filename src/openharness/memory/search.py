"""Simple heuristic memory search, augmented with BM25, RRF and Time Decay."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openharness.memory.scan import scan_memory_files
from openharness.memory.types import MemoryHeader
from openharness.memory.usage import get_memory_usage
from openharness.search.text import (
    compute_rrf_scores,
    contains_chinese,
    tokenize_for_bm25,
    tokenize_heuristic,
)

try:
    from rank_bm25 import BM25Okapi  # type: ignore
    HAS_BM25_JIEBA = True
except ImportError:
    HAS_BM25_JIEBA = False


# =========================================================================
# Scoring: RRF & Time Decay
# =========================================================================

_compute_rrf_scores = compute_rrf_scores  # backward-compatible alias
_tokenize_for_bm25 = tokenize_for_bm25    # backward-compatible alias
_tokenize_heuristic = tokenize_heuristic  # backward-compatible alias
_contains_chinese = contains_chinese      # backward-compatible alias


def _apply_time_decay(
    scores: list[float], 
    modified_ats: list[float], 
    half_life_days: float = 30.0
) -> list[float]:
    """Apply exponential time decay to scores based on age."""
    if not scores:
        return []
        
    current_time = time.time()
    decayed_scores = []
    
    # lambda = ln(2) / half_life_seconds
    decay_rate = math.log(2) / (half_life_days * 24 * 3600)
    
    for score, modified_at in zip(scores, modified_ats):
        if score <= 0.0:
            decayed_scores.append(0.0)
            continue
            
        age_seconds = max(0.0, current_time - modified_at)
        decay_factor = math.exp(-decay_rate * age_seconds)
        decayed_scores.append(score * decay_factor)
        
    return decayed_scores


# =========================================================================
# BM25 Cache
# =========================================================================

@dataclass
class BM25Index:
    """Container for BM25 indices and their cache invalidation signatures."""
    meta_okapi: Any | None
    body_okapi: Any | None
    file_hashes: dict[str, float]


class BM25CacheManager:
    """Manages the caching of BM25 indices to avoid rebuilding them on every query."""
    def __init__(self) -> None:
        self._cache: BM25Index | None = None

    def get_or_build_index(self, headers: list[MemoryHeader]) -> BM25Index | None:
        """Returns a valid BM25Index, using the cache if files haven't changed."""
        if not HAS_BM25_JIEBA:
            return None

        current_hashes = {str(h.path): h.modified_at for h in headers}

        # Check cache validity
        if self._cache is not None:
            if self._cache.file_hashes == current_hashes:
                return self._cache

        # Cache miss or invalid, rebuild index
        meta_corpus = []
        body_corpus = []
        for header in headers:
            meta = f"{header.title} {header.description}"
            body = header.body_preview
            meta_corpus.append(_tokenize_for_bm25(meta))
            body_corpus.append(_tokenize_for_bm25(body))

        meta_okapi = BM25Okapi(meta_corpus) if any(meta_corpus) else None
        body_okapi = BM25Okapi(body_corpus) if any(body_corpus) else None

        self._cache = BM25Index(
            meta_okapi=meta_okapi,
            body_okapi=body_okapi,
            file_hashes=current_hashes,
        )
        return self._cache


# Global cache instance
_global_bm25_cache = BM25CacheManager()


def _get_bm25_scores(query: str, headers: list[MemoryHeader]) -> tuple[list[float], list[float]]:
    """Returns (meta_scores, body_scores) for the given query and headers."""
    n = len(headers)
    if not HAS_BM25_JIEBA or n == 0:
        return [0.0] * n, [0.0] * n

    index = _global_bm25_cache.get_or_build_index(headers)
    if not index:
        return [0.0] * n, [0.0] * n

    bm25_query = _tokenize_for_bm25(query)

    scores_meta = index.meta_okapi.get_scores(bm25_query) if index.meta_okapi else [0.0] * n
    scores_body = index.body_okapi.get_scores(bm25_query) if index.body_okapi else [0.0] * n

    # `get_scores` might return a numpy array depending on rank_bm25 versions, safe to cast
    return list(scores_meta), list(scores_body)


# =========================================================================
# Main Search Entry
# =========================================================================

def find_relevant_memories(
    query: str,
    cwd: str | Path,
    *,
    max_results: int = 5,
) -> list[MemoryHeader]:
    """Return the memory files whose metadata and content overlap the query.

    Scoring is a hybrid approach combining BM25 and a simple token-frequency
    heuristic. They are fused using Reciprocal Rank Fusion (RRF), and then
    decayed based on the time since the memory was last modified.
    """
    heuristic_tokens = _tokenize_heuristic(query)
    if not heuristic_tokens:
        return []

    headers = list(scan_memory_files(cwd, max_files=100))
    if not headers:
        return []

    n = len(headers)

    # 1. Get BM25 Scores using cached index
    meta_bm25_scores, body_bm25_scores = _get_bm25_scores(query, headers)

    # BM25: Metadata matches are weighted 2x; body matches 1x
    bm25_combined = [
        meta * 2.0 + body 
        for meta, body in zip(meta_bm25_scores, body_bm25_scores)
    ]

    # 2. Get Heuristic Scores
    heuristic_combined = [0.0] * n
    for i, header in enumerate(headers):
        meta = f"{header.title} {header.description}".lower()
        body = header.body_preview.lower()

        # Heuristic: Metadata matches are weighted 2x; body matches 1x
        meta_hits = sum(1 for t in heuristic_tokens if t in meta)
        body_hits = sum(1 for t in heuristic_tokens if t in body)
        heuristic_combined[i] = float(meta_hits * 2.0 + body_hits)

    # 3. Fuse Scores using Reciprocal Rank Fusion (RRF)
    fused_scores = _compute_rrf_scores(bm25_combined, heuristic_combined)

    # 4. Apply Exponential Time Decay
    modified_ats = [header.modified_at for header in headers]
    final_scores = _apply_time_decay(fused_scores, modified_ats)

    # 5. Filter and Sort
    scored: list[tuple[float, MemoryHeader]] = []
    for i in range(n):
        # We only consider memories that had at least some non-zero base score
        if bm25_combined[i] > 0 or heuristic_combined[i] > 0:
            usage = get_memory_usage(cwd, headers[i].id, memory_dir=headers[i].path.parent)
            importance_boost = headers[i].importance * 0.4
            usage_boost = min(int(usage["use_count"]), 5) * 0.1
            scored.append((final_scores[i] + importance_boost + usage_boost, headers[i]))

    # Sort primarily by fused decayed score, tie-break by recent modified_at
    scored.sort(key=lambda item: (-item[0], -item[1].modified_at))
    return [header for _, header in scored[:max_results]]
