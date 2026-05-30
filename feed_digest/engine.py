"""Feed digest engine: orchestrates collect → normalize → AI pipeline → render → archive."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from openharness.utils.log import get_logger

from feed_digest.ai_pipeline import FeedDigestAIPipeline
from feed_digest.config import FeedDigestConfig
from feed_digest.models import FeedDigestResult, FeedItem, SourceStats
from feed_digest.presets import FeedPreset, get_preset
from feed_digest.render import format_digest_title, render_empty_digest, render_source_stats
from feed_digest.sources import get_source

logger = get_logger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_url(url: str) -> str:
    return url.split("#")[0].rstrip("/").lower()


def _allocate_slots(
    items: list[FeedItem],
    max_items: int,
    min_per_source: int,
) -> list[FeedItem]:
    """Allocate report slots guaranteeing minimum representation per source.

    Each source that has items gets at least ``min_per_source`` slots, or as
    many as possible without exceeding ``max_items``. Remaining slots are
    filled with the highest-scoring items from the global pool.
    """
    from collections import defaultdict

    by_source: dict[str, list[FeedItem]] = defaultdict(list)
    for item in items:
        by_source[item.source].append(item)

    active_sources = [s for s, its in by_source.items() if its]
    if not active_sources:
        return []

    # Reduce effective minimum if total would exceed max_items
    effective_min = min_per_source
    while effective_min > 0 and effective_min * len(active_sources) > max_items:
        effective_min -= 1

    if effective_min < 1:
        # Can't even fit one per source; fall back to global top
        return items[:max_items]

    result: list[FeedItem] = []
    used_urls: set[str] = set()

    # Phase 1: guarantee minimum per source
    for source in active_sources:
        for item in by_source[source][:effective_min]:
            if item.url not in used_urls:
                result.append(item)
                used_urls.add(item.url)

    # Phase 2: fill remaining slots with highest global scores
    remaining = [item for item in items if item.url not in used_urls]
    slots_left = max_items - len(result)
    if slots_left > 0:
        result.extend(remaining[:slots_left])

    return result


class FeedDigestEngine:
    """Orchestrates the full feed digest pipeline."""

    def __init__(
        self,
        *,
        config: FeedDigestConfig,
        provider_profile: str,
        source_configs: dict[str, Any] | None = None,
    ) -> None:
        self._config = config
        self._provider_profile = provider_profile
        self._source_configs = source_configs or {}

    def _resolve_domain(self, domain_name: str) -> FeedPreset:
        """Resolve a domain name to a FeedPreset.

        Checks user-defined ``config.domains`` first; falls back to the
        built-in preset registry (e.g. ``ai_news``).
        """
        user_domain = self._config.domains.get(domain_name)
        if user_domain is not None:
            # Use the English domain descriptor for search quality; fall back to title.
            search_domain = user_domain.domain or user_domain.title
            return FeedPreset(
                name=domain_name,
                domain=search_domain,
                title_template=user_domain.title + "简报 {date}",
                description="",
                sources=user_domain.sources,
                source_weights=user_domain.source_weights,
            )
        return get_preset(domain_name)

    def _get_domain_source_configs(self, domain_name: str) -> dict[str, dict]:
        """Return per-source config overrides for the given domain."""
        user_domain = self._config.domains.get(domain_name)
        if user_domain is not None:
            return user_domain.source_configs
        return {}

    async def run(
        self,
        *,
        domain_name: str | None = None,
        # backward-compat alias
        preset_name: str | None = None,
        date: str | None = None,
        progress_callback: Callable[[str], Any] | None = None,
    ) -> FeedDigestResult:
        """Run one feed digest cycle for a single domain."""

        async def _notify(text: str) -> None:
            if progress_callback is None:
                return
            try:
                import asyncio

                if asyncio.iscoroutine(coro := progress_callback(text)):
                    await coro
            except Exception:
                pass

        effective_name = domain_name or preset_name
        if not effective_name:
            effective_name = self._config.enable_domains[0] if self._config.enable_domains else "ai_news"

        preset = self._resolve_domain(effective_name)
        domain_source_configs = self._get_domain_source_configs(effective_name)
        now = _utcnow()
        run_date = date or now.strftime("%Y-%m-%d")
        since = (now - timedelta(hours=self._config.lookback_hours)).isoformat()
        until = now.isoformat()

        logger.info("FeedDigestEngine.run domain=%s date=%s", preset.name, run_date)

        source_names = preset.sources
        all_items: list[FeedItem] = []
        source_stats: list[SourceStats] = []

        async def _collect_one(name: str) -> tuple[str, list[FeedItem], str]:
            try:
                # Merge engine-level source config with domain-level overrides
                merged_cfg = {
                    **self._source_configs.get(name, {}),
                    **domain_source_configs.get(name, {}),
                }
                source = get_source(name, merged_cfg)
                items = await source.collect(
                    since=since,
                    until=until,
                    domain=preset.domain,
                    query=preset.domain,
                    max_items=self._config.max_candidates // max(len(source_names), 1) + 5,
                )
                return name, items, ""
            except Exception as exc:
                logger.warning("Source %r collect failed: %s", name, exc)
                return name, [], str(exc)

        await _notify(f"📡 正在采集新闻源…（{len(source_names)} 个来源，预计 5-15 秒）")
        results = await asyncio.gather(
            *[_collect_one(name) for name in source_names],
            return_exceptions=False,
        )
        for name, items, err in results:
            weight = preset.source_weights.get(name, 1.0)
            for item in items:
                item.domain = preset.domain
                item.preset = preset.name
                if weight != 1.0:
                    item.score = item.score * weight
            all_items.extend(items)
            source_stats.append(
                SourceStats(
                    source=name,
                    fetched=len(items),
                    failed=bool(err),
                    warning=err[:200] if err else "",
                )
            )

        seen_urls: set[str] = set()
        deduped: list[FeedItem] = []
        for item in all_items:
            key = _normalize_url(item.url)
            if key not in seen_urls and item.url and item.title:
                seen_urls.add(key)
                deduped.append(item)

        candidates = deduped[: self._config.max_candidates]
        warnings: list[str] = []

        if not candidates:
            for stat in source_stats:
                if stat.failed:
                    warnings.append(f"Source {stat.source} failed: {stat.warning}")
            title = format_digest_title(preset.title_template, run_date)
            return FeedDigestResult(
                date=run_date,
                domain=preset.domain,
                preset=preset.name,
                period_start=since,
                period_end=until,
                source_stats=source_stats,
                warnings=warnings,
                is_empty=True,
                markdown=render_empty_digest(title, warnings),
            )

        pipeline = FeedDigestAIPipeline(
            profile=self._provider_profile,
        )

        await _notify(f"🔍 AI 评分过滤，共 {len(candidates)} 条候选…（预计 20-40 秒）")
        try:
            scored = await pipeline.score_and_filter(
                candidates,
                domain=preset.domain,
                min_relevance=self._config.min_relevance_score,
                min_signal=self._config.min_signal_score,
                min_per_source=self._config.min_per_source,
            )
            logger.info("After scoring: %d/%d items passed", len(scored), len(candidates))
        except Exception as exc:
            logger.warning("AI scoring failed, using unscored candidates: %s", exc)
            scored = candidates
            warnings.append(f"AI scoring failed: {exc}")

        # Fallback: if AI scoring filtered everything, take top candidates by
        # heuristic score so the digest is never empty when items exist.
        if not scored and candidates:
            from feed_digest.ai_pipeline import _heuristic_score

            logger.warning(
                "AI scoring filtered all %d candidates — falling back to heuristic top %d",
                len(candidates),
                self._config.max_items,
            )
            for item in candidates:
                item.score = _heuristic_score(item)
            scored = sorted(candidates, key=lambda x: x.score, reverse=True)[: self._config.max_items]

        if not scored and self._config.allow_empty_digest:
            title = format_digest_title(preset.title_template, run_date)
            return FeedDigestResult(
                date=run_date,
                domain=preset.domain,
                preset=preset.name,
                period_start=since,
                period_end=until,
                items=candidates,
                selected_items=[],
                source_stats=source_stats,
                warnings=warnings,
                is_empty=True,
                markdown=render_empty_digest(title, warnings + ["今日无高信号内容，未推送简报"]),
            )

        await _notify(f"🔄 AI 语义去重，已筛选 {len(scored)} 条…")
        try:
            final_items = await pipeline.deduplicate(scored)
            logger.info("After dedup: %d items", len(final_items))
        except Exception as exc:
            logger.warning("AI dedup failed: %s", exc)
            final_items = scored
            warnings.append(f"AI dedup failed: {exc}")

        allocated_items = _allocate_slots(
            final_items,
            max_items=self._config.max_items,
            min_per_source=self._config.min_per_source,
        )
        logger.info("After slot allocation: %d items", len(allocated_items))

        for stat in source_stats:
            stat.selected = sum(1 for item in allocated_items if item.source == stat.source)

        title = format_digest_title(preset.title_template, run_date)
        await _notify(f"✍️ AI 综合撰写简报，{len(allocated_items)} 条精选内容…（预计 20-40 秒）")
        try:
            markdown, trends = await pipeline.synthesize(
                allocated_items,
                title=title,
                domain=preset.domain,
                max_items=self._config.max_items,
                max_trends=self._config.max_trends,
                source_stats=source_stats,
                total_candidates=len(candidates),
            )
        except Exception as exc:
            logger.warning("AI synthesis failed: %s", exc)
            markdown = f"# {title}\n\n_生成失败: {exc}_"
            trends = []
            warnings.append(f"AI synthesis failed: {exc}")

        stats_section = f"\n\n## 来源统计\n{render_source_stats(source_stats)}"
        if "来源统计" not in markdown:
            markdown += stats_section

        return FeedDigestResult(
            date=run_date,
            domain=preset.domain,
            preset=preset.name,
            period_start=since,
            period_end=until,
            items=candidates,
            selected_items=allocated_items,
            trends=trends,
            markdown=markdown,
            source_stats=source_stats,
            warnings=warnings,
            is_empty=False,
        )
