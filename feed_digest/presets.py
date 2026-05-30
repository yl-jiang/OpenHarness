"""Feed digest presets. Each preset defines a domain, sources, and weights."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FeedPreset:
    name: str
    domain: str
    title_template: str
    description: str
    sources: list[str] = field(default_factory=list)
    source_weights: dict[str, float] = field(default_factory=dict)


AI_NEWS = FeedPreset(
    name="ai_news",
    domain="AI & Machine Learning",
    title_template="AI 热点简报 {date}",
    description="AI 相关热点新闻、技术动态、开源项目、研究成果",
    sources=["github", "hackernews", "rss", "huggingface", "36kr", "ithome", "newsnow", "sopilot", "v2ex"],
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
)

POLITICS = FeedPreset(
    name="politics",
    domain="Politics & Geopolitics",
    title_template="政治要闻简报 {date}",
    description="国际政治、地缘博弈、外交动态、重大政策、选举与政府事务",
    sources=["hackernews", "rss"],
    source_weights={"hackernews": 1.0, "rss": 1.2},
)

FINANCE = FeedPreset(
    name="finance",
    domain="Finance & Economy",
    title_template="金融财经简报 {date}",
    description="金融市场、宏观经济、央行政策、IPO、并购、加密货币、投资动态",
    sources=["hackernews", "rss", "36kr"],
    source_weights={"hackernews": 1.1, "rss": 1.2, "36kr": 1.0},
)

TECH = FeedPreset(
    name="tech",
    domain="Technology",
    title_template="科技动态简报 {date}",
    description="软件工程、硬件芯片、科技公司动向、行业趋势、产品发布、开源生态",
    sources=["github", "hackernews", "rss", "v2ex", "36kr"],
    source_weights={"github": 1.2, "hackernews": 1.1, "rss": 1.0, "v2ex": 0.9, "36kr": 1.0},
)

PRESET_REGISTRY: dict[str, FeedPreset] = {
    "ai_news": AI_NEWS,
    "politics": POLITICS,
    "finance": FINANCE,
    "tech": TECH,
}


def get_preset(name: str) -> FeedPreset:
    if name not in PRESET_REGISTRY:
        raise ValueError(f"Unknown feed preset: {name!r}. Available: {list(PRESET_REGISTRY)}")
    return PRESET_REGISTRY[name]
