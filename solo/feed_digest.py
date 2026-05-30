"""Feed digest integration for the solo app."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from openharness.utils.log import get_logger

from feed_digest.engine import FeedDigestEngine
from feed_digest.models import FeedDigestResult
from solo.config import load_config
from solo.core.models import SoloReport
from solo.core.store import SoloStore

logger = get_logger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _combine_results(results: list[FeedDigestResult]) -> FeedDigestResult:
    """Merge multiple single-domain results into one combined report."""
    non_empty = [r for r in results if not r.is_empty]
    if not non_empty:
        base = results[0]
        return FeedDigestResult(
            date=base.date,
            domain=", ".join(r.domain for r in results),
            preset=", ".join(r.preset for r in results),
            period_start=base.period_start,
            period_end=base.period_end,
            source_stats=[s for r in results for s in r.source_stats],
            warnings=[w for r in results for w in r.warnings],
            is_empty=True,
            markdown="\n\n---\n\n".join(r.markdown for r in results),
        )

    sections: list[str] = []
    all_source_stats = []
    all_warnings: list[str] = []
    all_items = []
    all_selected = []
    all_trends: list[str] = []

    for r in results:
        sections.append(r.markdown)
        all_source_stats.extend(r.source_stats)
        all_warnings.extend(r.warnings)
        all_items.extend(r.items)
        all_selected.extend(r.selected_items)
        all_trends.extend(r.trends)

    combined_markdown = "\n\n---\n\n".join(sections)
    base = non_empty[0]
    return FeedDigestResult(
        date=base.date,
        domain=", ".join(r.domain for r in results),
        preset=", ".join(r.preset for r in results),
        period_start=base.period_start,
        period_end=base.period_end,
        items=all_items,
        selected_items=all_selected,
        trends=all_trends,
        markdown=combined_markdown,
        source_stats=all_source_stats,
        warnings=all_warnings,
        is_empty=False,
    )


async def run_feed_digest(
    *,
    workspace: str | Path | None = None,
    domain_name: str | None = None,
    # backward-compat alias
    preset_name: str | None = None,
    date: str | None = None,
    progress_callback: Callable[[str], Any] | None = None,
) -> SoloReport:
    """Run feed digest for one or all enabled domains and archive the result.

    When ``domain_name`` (or the deprecated ``preset_name``) is given, only
    that domain is run.  Otherwise all ``enable_domains`` from the config are
    run in parallel and their reports are merged.
    """
    config = load_config(workspace)
    fd_config = config.feed_digest

    engine = FeedDigestEngine(
        config=fd_config,
        provider_profile=config.provider_profile,
    )

    effective_domain = domain_name or preset_name
    domains_to_run = [effective_domain] if effective_domain else list(fd_config.enable_domains or ["ai_news"])

    if len(domains_to_run) == 1:
        result = await engine.run(
            domain_name=domains_to_run[0],
            date=date,
            progress_callback=progress_callback,
        )
    else:
        raw = await asyncio.gather(
            *[engine.run(domain_name=d, date=date, progress_callback=progress_callback) for d in domains_to_run],
            return_exceptions=False,
        )
        result = _combine_results(list(raw))

    report = SoloReport(
        id=uuid4().hex[:12],
        report_type="feed_digest",
        content=result.markdown,
        created_at=_now(),
        period_start=result.period_start,
        period_end=result.period_end,
        metadata={
            "preset": result.preset,
            "domain": result.domain,
            "date": result.date,
            "is_empty": result.is_empty,
            "selected_count": len(result.selected_items),
            "source_stats": [
                {
                    "source": stat.source,
                    "fetched": stat.fetched,
                    "selected": stat.selected,
                    "failed": stat.failed,
                }
                for stat in result.source_stats
            ],
            "warnings": result.warnings,
        },
    )

    if fd_config.archive_enabled:
        store = SoloStore(workspace)
        store.add_report(report)
        logger.info(
            "solo feed digest archived id=%s domain=%s date=%s items=%d",
            report.id,
            result.domain,
            result.date,
            len(result.selected_items),
        )

    return report
