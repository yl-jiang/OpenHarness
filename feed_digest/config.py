"""FeedDigestConfig - configures the feed digest cron job."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainConfig(BaseModel):
    """Configuration for one feed domain profile.

    ``title`` is the human-visible display name (e.g. "AI 热点").
    ``domain`` is the English semantic descriptor injected into AI prompts for
    relevance scoring. Defaults to ``title`` when not set, but it is strongly
    recommended to provide an explicit English value.

    ``objective`` is the research contract for the OpenCLI+LLM pipeline.
    The LLM autonomously selects which OpenCLI adapters and commands to use
    based on the full catalog; no source allowlist/blocklist is needed.

    ``seed_actions`` are mandatory OpenCLI commands that always run before the
    LLM planning loop, regardless of what the model chooses.  Each entry is a
    dict with keys ``site``, ``command``, ``args`` (list), and optional
    ``source`` (display name; defaults to ``site``).

    Example config::

        [feed_digest.domains.ai_news]
        title = "AI 热点"
        domain = "AI & Machine Learning"
        objective = "Collect high-signal AI and ML news..."

        [[feed_digest.domains.ai_news.seed_actions]]
        site = "web"
        command = "read"
        args = ["--url", "https://github.com/trending", "--stdout", "true"]
        source = "github_trending"
    """

    model_config = ConfigDict(extra="ignore")

    title: str
    domain: str = ""
    objective: str = ""
    seed_actions: list[dict[str, Any]] = Field(default_factory=list)


class ResearchBudget(BaseModel):
    """Bounded budget for LLM-managed OpenCLI research."""

    max_rounds: int = 4
    max_actions: int = 16
    max_actions_per_round: int = 6
    min_unique_sources: int = 4
    command_timeout_seconds: int = 90
    registry_timeout_seconds: int = 300
    max_output_chars: int = 12_000


class ResearchConfig(BaseModel):
    """Global policy for agentic OpenCLI research."""

    objective: str = (
        "Collect high-signal, recent information for the requested digest domain. "
        "Prefer primary sources, concrete launches, research, tools, incidents, and measurable trends."
    )
    prefer_public_adapters: bool = True
    allow_browser_adapters: bool = True
    budget: ResearchBudget = Field(default_factory=ResearchBudget)


def _default_domains() -> dict[str, DomainConfig]:
    return {
        "ai_news": DomainConfig(
            title="AI 热点",
            domain="AI & Machine Learning",
            objective=(
                "Collect high-signal AI and machine learning news from the last 24 hours. "
                "Prioritize model releases, agent products, developer tools, important papers, "
                "open-source launches, safety incidents, funding or market moves with concrete facts. "
                "Use diverse sources: tech news sites, academic preprints, developer communities, "
                "Chinese AI media, and social aggregators."
            ),
            seed_actions=[
                {
                    "site": "web",
                    "command": "read",
                    "args": ["--url", "https://github.com/trending", "--stdout", "true"],
                    "source": "github_trending",
                }
            ],
        ),
        "politics": DomainConfig(
            title="政治要闻",
            domain="Politics & Geopolitics",
            objective=(
                "Collect high-signal international politics and geopolitics news with concrete actors, "
                "policy moves, elections, diplomatic events, and security developments."
            ),
        ),
        "finance": DomainConfig(
            title="金融财经",
            domain="Finance & Economy",
            objective=(
                "Collect high-signal finance and economy news about markets, central banks, public companies, "
                "IPOs, crypto, regulation, and macro indicators."
            ),
        ),
        "tech": DomainConfig(
            title="科技动态",
            domain="Technology",
            objective=(
                "Collect high-signal technology news about software engineering, hardware, developer tools, "
                "open source, product launches, and major platform changes."
            ),
        ),
    }


class FeedDigestConfig(BaseModel):
    enabled: bool = True
    schedule: str = "30 21 * * *"
    timezone: str = "Asia/Shanghai"
    lookback_hours: int = 24
    domains: dict[str, DomainConfig] = Field(default_factory=_default_domains)
    enable_domains: list[str] = Field(default_factory=lambda: ["ai_news"])
    research: ResearchConfig = Field(default_factory=ResearchConfig)
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
        """Ensure built-in domains are present and key new fields are filled.

        - Missing domain keys are inserted wholesale.
        - Existing domains get ``domain``, ``objective``, and ``seed_actions``
          backfilled from defaults when those fields are empty (non-destructive).
        """
        defaults = _default_domains()
        for key, default_domain in defaults.items():
            if key not in self.domains:
                self.domains[key] = default_domain
                continue
            existing = self.domains[key]
            if not existing.domain and default_domain.domain:
                existing.domain = default_domain.domain
            if not existing.objective and default_domain.objective:
                existing.objective = default_domain.objective
            if not existing.seed_actions and default_domain.seed_actions:
                existing.seed_actions = default_domain.seed_actions
        return self
