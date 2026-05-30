"""FeedDigestConfig - configures the feed digest cron job."""
from __future__ import annotations

from copy import deepcopy

from pydantic import BaseModel, Field, model_validator

AI_QUERY_TERMS = [
    "AI",
    "人工智能",
    "大模型",
    "LLM",
    "Agent",
    "OpenAI",
    "Anthropic",
    "Claude",
    "Gemini",
    "DeepSeek",
    "GLM",
    "Mimo",
    "MiniMax",
    "Bytedance",
]


class DomainConfig(BaseModel):
    """Configuration for one feed domain profile.

    ``title`` is the human-visible display name (e.g. "AI 热点").
    ``domain`` is the English semantic descriptor used as the search query for
    external content sources (GitHub, HackerNews, RSS) and injected into AI
    prompts for relevance scoring.  Defaults to ``title`` when not set, but
    it is strongly recommended to provide an English value so that English
    content sources return results.

    ``source_configs`` holds per-source configuration dicts, e.g.::

        source_configs = {
            "rss": {"feeds": ["https://..."]},
            "arxiv": {"categories": ["cs.AI", "cs.CL"]},
            "36kr": {"category": "AI"},
        }

    Example config::

        [feed_digest.domains.ai_news]
        title = "AI 热点"
        domain = "AI & Machine Learning"
        sources = ["github", "hackernews", "rss", "huggingface"]
    """

    title: str
    domain: str = ""  # English search descriptor; falls back to title when empty
    sources: list[str] = Field(default_factory=lambda: ["github", "hackernews", "rss"])
    source_weights: dict[str, float] = Field(default_factory=dict)
    source_configs: dict[str, dict] = Field(default_factory=dict)


def _default_domains() -> dict[str, DomainConfig]:
    return {
        "ai_news": DomainConfig(
            title="AI 热点",
            domain="AI & Machine Learning",
            sources=[
                "github",
                "hackernews",
                "rss",
                "huggingface",
                "36kr",
                "ithome",
                "newsnow",
                "sopilot",
                "v2ex",
            ],
            source_weights={
                "github": 1.2,
                "hackernews": 1.0,
                "rss": 1.0,
                "huggingface": 1.3,
                "36kr": 0.9,
                "ithome": 1.0,
                "newsnow": 0.95,
                "sopilot": 1.05,
                "v2ex": 0.9,
            },
            source_configs={
                "hackernews": {"mode": "top_best", "query_terms": AI_QUERY_TERMS},
                "rss": {
                    "feeds": [
                        "https://www.jiqizhixin.com/rss",
                        "https://feeds.feedburner.com/oreilly/radar",
                        "https://blog.openai.com/rss/",
                        "https://www.deepmind.com/blog/rss.xml",
                    ]
                },
                "36kr": {"category": "AI"},
                "ithome": {"tag": "AI", "query_terms": AI_QUERY_TERMS},
                "newsnow": {
                    "groups": ["ai_news"],
                    "query_terms": AI_QUERY_TERMS,
                    "max_sources": 10,
                },
                "sopilot": {"query_terms": AI_QUERY_TERMS, "prefer_rss": True},
            },
        ),
        "politics": DomainConfig(
            title="政治要闻",
            domain="Politics & Geopolitics",
            sources=["hackernews", "rss"],
            source_weights={"hackernews": 1.0, "rss": 1.2},
            source_configs={
                "rss": {
                    "feeds": [
                        "https://feeds.bbci.co.uk/news/world/rss.xml",
                        "https://feeds.reuters.com/Reuters/worldNews",
                        "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
                        "https://www.ft.com/?format=rss",
                    ]
                },
            },
        ),
        "finance": DomainConfig(
            title="金融财经",
            domain="Finance & Economy",
            sources=["hackernews", "rss", "36kr"],
            source_weights={"hackernews": 1.1, "rss": 1.2, "36kr": 1.0},
            source_configs={
                "rss": {
                    "feeds": [
                        "https://feeds.reuters.com/reuters/businessNews",
                        "https://feeds.bloomberg.com/markets/news.rss",
                        "https://www.wsj.com/xml/rss/3_7085.xml",
                    ]
                },
                "36kr": {"category": "zhengquan"},
            },
        ),
        "tech": DomainConfig(
            title="科技动态",
            domain="Technology",
            sources=["github", "hackernews", "rss", "v2ex", "36kr"],
            source_weights={"github": 1.2, "hackernews": 1.1, "rss": 1.0, "v2ex": 0.9, "36kr": 1.0},
            source_configs={
                "rss": {
                    "feeds": [
                        "https://feeds.arstechnica.com/arstechnica/index",
                        "https://www.wired.com/feed/rss",
                        "https://techcrunch.com/feed/",
                    ]
                },
                "36kr": {"category": "keji"},
            },
        ),
    }


class FeedDigestConfig(BaseModel):
    enabled: bool = True
    schedule: str = "30 21 * * *"
    timezone: str = "Asia/Shanghai"
    lookback_hours: int = 24
    # Domain profiles: each key is a domain ID. Built-in domains (ai_news,
    # politics, finance, tech) are pre-populated so they appear in the user's
    # config file and can be freely edited or extended.
    domains: dict[str, DomainConfig] = Field(default_factory=_default_domains)
    # Domain IDs to activate on each scheduled run. Domains are run in
    # parallel and their reports are merged into one digest.
    enable_domains: list[str] = Field(default_factory=lambda: ["ai_news"])
    max_candidates: int = 90
    max_items: int = 30
    max_trends: int = 8
    min_relevance_score: float = 0.3
    min_signal_score: float = 0.2
    min_per_source: int = 5
    dedupe_similarity_threshold: float = 0.86
    allow_empty_digest: bool = True
    archive_enabled: bool = True
    im_push_enabled: bool = True

    @model_validator(mode="after")
    def _backfill_default_domains(self) -> "FeedDigestConfig":
        """Ensure built-in domains are present and up-to-date even when loading an
        old config file.

        - Missing domain keys are inserted wholesale.
        - Existing domains get field-level backfill for **newly-added fields only**:
          * ``domain`` (English search descriptor) — filled when empty; previously
            this field didn't exist so old configs always have it as "".
          * ``source_configs`` — missing source entries and missing config keys are
            backfilled non-destructively.
          * ``source_weights`` — new source entries are added; existing kept intact.

        The ``sources`` list is intentional user configuration and is never modified.
        """
        defaults = _default_domains()
        for key, default_domain in defaults.items():
            if key not in self.domains:
                self.domains[key] = default_domain
                continue

            existing = self.domains[key]

            # Backfill English search descriptor (new field — old configs have "")
            if not existing.domain and default_domain.domain:
                existing.domain = default_domain.domain

            # Backfill source_configs without overwriting user-provided values.
            for src, default_cfg in default_domain.source_configs.items():
                existing_cfg = existing.source_configs.get(src)
                if existing_cfg is None:
                    existing.source_configs[src] = deepcopy(default_cfg)
                    continue
                if isinstance(existing_cfg, dict) and isinstance(default_cfg, dict):
                    for cfg_key, cfg_value in default_cfg.items():
                        existing_cfg.setdefault(cfg_key, deepcopy(cfg_value))

            # Backfill source weights for any new sources (non-destructive)
            for src, weight in default_domain.source_weights.items():
                existing.source_weights.setdefault(src, weight)

        return self
