"""Tests for feed digest config (sources.py hand-crafted collectors removed)."""
from __future__ import annotations

from feed_digest.config import FeedDigestConfig


def test_default_ai_news_domain_has_required_fields() -> None:
    domain = FeedDigestConfig().domains["ai_news"]
    assert domain.title == "AI 热点"
    assert domain.domain == "AI & Machine Learning"
    assert domain.objective


def test_backfill_adds_missing_domain_fields_non_destructively() -> None:
    config = FeedDigestConfig(
        domains={
            "ai_news": {
                "title": "AI 热点",
                "domain": "",
            }
        }
    )
    domain = config.domains["ai_news"]
    assert domain.domain == "AI & Machine Learning"
    assert domain.objective
