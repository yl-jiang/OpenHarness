"""Markdown rendering helpers for feed digest."""
from __future__ import annotations

from feed_digest.models import SourceStats


def render_source_stats(stats: list[SourceStats]) -> str:
    """Render source stats as clean markdown — no failure marks per skill spec."""
    lines = []
    for item in stats:
        if item.failed:
            continue  # Omit failed sources from report output
        lines.append(f"- {item.source}: 抓取 {item.fetched} 条，入选 {item.selected} 条")
    return "\n".join(lines) if lines else "- 无来源统计"


def render_source_stats_header(stats: list[SourceStats]) -> str:
    """One-line header summary: '覆盖 N 个数据源 · 聚合 M 条原始信息'."""
    active = [s for s in stats if not s.failed]
    total_fetched = sum(s.fetched for s in active)
    return f"覆盖 {len(active)} 个数据源 · 聚合 {total_fetched} 条原始信息"


def render_empty_digest(title: str, warnings: list[str]) -> str:
    lines = [f"# {title}", "", "> 📭 今日无高信号简报", ""]
    return "\n".join(lines)


def format_digest_title(template: str, date: str) -> str:
    return template.format(date=date)

