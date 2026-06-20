"""Text tokenization helpers for project AI features.

Used by solo/wolo stores for BM25 record search.
"""

from __future__ import annotations

import re


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
