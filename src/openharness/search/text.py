"""Shared text-search primitives (tokenization, RRF fusion).

Used by both ``openharness.memory.search`` and ``openharness.skills.search``
so the BM25 + heuristic hybrid strategy is implemented once.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

try:
    import jieba  # type: ignore

    _HAS_JIEBA = True
    jieba.setLogLevel(logging.INFO)
except ImportError:
    _HAS_JIEBA = False


_STOP_WORDS_PATH = Path(__file__).parent / "stopwords.txt"
_STOP_WORDS: set[str] | None = None


def contains_chinese(text: str) -> bool:
    """Return True when *text* contains at least one Han ideograph."""
    return bool(re.search(r"[\u4e00-\u9fff\u3400-\u4dbf]", text))


def load_stop_words(path: Path | None = None) -> set[str]:
    """Load the stop-word list from *path* (cached after first call).

    When *path* is ``None`` the bundled ``stopwords.txt`` next to this module
    is used. Lines beginning with ``#`` and blank lines are ignored.
    """
    global _STOP_WORDS
    target = path or _STOP_WORDS_PATH
    if _STOP_WORDS is not None:
        return _STOP_WORDS

    words: set[str] = set()
    if target.exists():
        for line in target.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.add(line.lower())
    _STOP_WORDS = words
    return _STOP_WORDS


def tokenize_for_bm25(text: str) -> list[str]:
    """Tokenize *text* for BM25 indexing, filtering stop words.

    Uses ``jieba`` when the text contains Chinese so word boundaries match
    natural segmentation; falls back to ASCII alphanumerics otherwise.
    """
    text = text.lower()
    if _HAS_JIEBA and contains_chinese(text):
        words = list(jieba.cut(text))  # type: ignore
    else:
        words = re.findall(r"[A-Za-z0-9_]+", text)

    stop_words = load_stop_words()
    return [w for w in words if w not in stop_words and w.strip()]


def tokenize_heuristic(text: str) -> set[str]:
    """Extract coarse search tokens (ASCII words, Chinese words, Han chars)."""
    text = text.lower()
    ascii_tokens = {t for t in re.findall(r"[A-Za-z0-9_]+", text) if len(t) >= 3}

    if _HAS_JIEBA and contains_chinese(text):
        han_words = {w for w in jieba.cut(text) if contains_chinese(w)}  # type: ignore
        ascii_tokens.update(han_words)

    han_chars = set(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text))

    tokens = ascii_tokens | han_chars
    stop_words = load_stop_words()
    return {t for t in tokens if t not in stop_words and t.strip()}


def compute_rrf_scores(
    bm25_scores: list[float],
    heuristic_scores: list[float],
    k: int = 60,
) -> list[float]:
    """Reciprocal Rank Fusion across two score lists.

    Entries that score zero in both inputs receive zero in the output so
    non-matching documents do not pollute the ranking.
    """
    if not bm25_scores or not heuristic_scores:
        return []

    n = len(bm25_scores)

    def get_ranks(scores: list[float]) -> dict[int, int]:
        indices = sorted(range(n), key=lambda i: scores[i], reverse=True)
        ranks: dict[int, int] = {}
        current_rank = 1
        for i, idx in enumerate(indices):
            if i > 0 and scores[idx] == scores[indices[i - 1]]:
                ranks[idx] = ranks[indices[i - 1]]
            else:
                ranks[idx] = current_rank
            current_rank += 1
        return ranks

    bm25_ranks = get_ranks(bm25_scores)
    heuristic_ranks = get_ranks(heuristic_scores)

    rrf_scores = [0.0] * n
    for i in range(n):
        if bm25_scores[i] <= 0.0 and heuristic_scores[i] <= 0.0:
            rrf_scores[i] = 0.0
            continue
        r_bm25 = bm25_ranks[i]
        r_heur = heuristic_ranks[i]
        rrf_scores[i] = (1.0 / (k + r_bm25)) + (1.0 / (k + r_heur))

    return rrf_scores


__all__ = [
    "compute_rrf_scores",
    "contains_chinese",
    "load_stop_words",
    "tokenize_for_bm25",
    "tokenize_heuristic",
]
