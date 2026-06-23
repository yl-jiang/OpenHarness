"""Deterministic Markdown rendering from InsightReport JSON."""
from __future__ import annotations

from typing import Any


def render_insight_markdown(insight: dict[str, Any], domain: str, report_type: str) -> str:
    """Render InsightReport JSON to Markdown for fallback / export / IM push."""
    lines: list[str] = []

    period_label = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}.get(report_type, report_type)
    domain_label = {"health": "健康", "finance": "财务"}.get(domain, domain)
    headline = insight.get("headline", "")
    narrative = insight.get("narrative", "")

    lines.append(f"# 🌱 {domain_label}{period_label}洞察\n")
    if headline:
        lines.append(f"**{headline}**\n")
    if narrative:
        lines.append(f"{narrative}\n")

    # Period comparison
    comparisons = insight.get("period_comparison", [])
    if comparisons:
        lines.append("## 📊 周期对比\n")
        lines.append("| 指标 | 本期 | 上期 | 变化 |")
        lines.append("|------|------|------|------|")
        for c in comparisons:
            arrow = {"up": "↑", "down": "↓", "flat": "→"}.get(c.get("direction", ""), "")
            unit = c.get("unit", "")
            lines.append(
                f"| {c['metric']} | {c['current']}{unit} | {c['previous']}{unit} "
                f"| {arrow}{abs(c.get('delta_pct', 0)):.1f}% |"
            )
        lines.append("")

    # Blind spots
    blind_spots = insight.get("blind_spots", [])
    if blind_spots:
        lines.append("## 🕳️ 你可能忽视的\n")
        for bs in blind_spots:
            icon = {"alert": "🔴", "watch": "🟡", "info": "ℹ️"}.get(bs.get("severity", "info"), "ℹ️")
            lines.append(f"**{icon} {bs['title']}**\n{bs['why']}\n> 证据：{bs['evidence']}\n")
        lines.append("")

    # Insights
    insights = insight.get("insights", [])
    if insights:
        lines.append("## 🔍 深度洞察\n")
        for ins in insights:
            icon = ins.get("icon", "🔍")
            lines.append(f"### {icon} {ins['title']}\n{ins['analysis']}\n")
            evidence = ins.get("evidence", [])
            if evidence:
                lines.append("证据：" + " | ".join(str(e) for e in evidence) + "\n")
        lines.append("")

    # Patterns
    patterns = insight.get("patterns", [])
    if patterns:
        lines.append("## 🔗 模式识别\n")
        for p in patterns:
            strength_icon = {"strong": "●●●", "moderate": "●●○", "weak": "●○○"}.get(p["strength"], "●○○")
            lines.append(f"- **{p['name']}** [{strength_icon}] {p['detail']}")
        lines.append("")

    # Recommendations
    recs = insight.get("recommendations", [])
    if recs:
        lines.append("## 💡 行动建议\n")
        for i, r in enumerate(recs, 1):
            lines.append(
                f"{i}. **{r['action']}** — {r['rationale']} "
                f"— 验证信号：{r.get('expected_signal', '—')}"
            )
        lines.append("")

    return "\n".join(lines)
