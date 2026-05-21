"""Utility functions for solo."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _now() -> str:
    """Get current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


def _get_weekday(date_str: str) -> str:
    """Calculate Chinese weekday name from date string."""
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(date_str)
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return weekdays[dt.weekday()]
    except Exception:
        return ""


def _get_period(created_at: str) -> str:
    """Determine the time period of the day from ISO created_at string."""
    try:
        dt = datetime.fromisoformat(created_at)
        hour = dt.hour
        if 0 <= hour < 5:
            return "凌晨"
        if 5 <= hour < 9:
            return "清晨"
        if 9 <= hour < 12:
            return "上午"
        if 12 <= hour < 14:
            return "中午"
        if 14 <= hour < 18:
            return "下午"
        if 18 <= hour < 22:
            return "傍晚"
        return "深夜"
    except Exception:
        return ""


def _get_season(date_str: str) -> str:
    """Determine the season from date string."""
    try:
        if len(date_str) == 10:
            month = int(date_str[5:7])
        else:
            dt = datetime.fromisoformat(date_str)
            month = dt.month
        
        if month in {3, 4, 5}:
            return "春季"
        if month in {6, 7, 8}:
            return "夏季"
        if month in {9, 10, 11}:
            return "秋季"
        return "冬季"
    except Exception:
        return ""


def _is_weekend(date_str: str) -> bool:
    """Check if the date falls on a weekend."""
    try:
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(date_str)
        return dt.weekday() >= 5  # 5 is Saturday, 6 is Sunday
    except Exception:
        return False


def _get_holiday(date_str: str) -> str | None:
    """Identify Chinese holidays (Lunar/Solar) and Solar Terms using lunar-python."""
    try:
        from lunar_python import Lunar
        
        if len(date_str) == 10:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        else:
            dt = datetime.fromisoformat(date_str)
        
        lunar = Lunar.fromDate(dt)
        all_events: list[str] = []
        
        # 1. Lunar Festivals (e.g., 春节, 中秋)
        all_events.extend(lunar.getFestivals())
        
        # 2. Solar (Gregorian) Festivals (e.g., 元旦, 劳动节)
        all_events.extend(lunar.getSolar().getFestivals())
        
        # 3. Solar Terms (24节气, e.g., 立春, 清明)
        jie_qi = lunar.getJieQi()
        if jie_qi:
            all_events.append(jie_qi)
            
        if not all_events:
            return None
            
        # Deduplicate and join
        return ", ".join(dict.fromkeys(all_events))
    except Exception:
        return None


def _get_personal_events(workspace: str | Path | None, date_str: str) -> str | None:
    """Return personal important-date labels that match *date_str* (YYYY-MM-DD).

    Reads ``user.md`` from the workspace and looks for lines inside an
    ``## Important dates`` section of the form::

        Label: MM-DD
        Label: YYYY-MM-DD

    Recurring dates (``MM-DD``) match any year; full dates (``YYYY-MM-DD``)
    also match every year on that month-day.
    """
    try:
        if len(date_str) == 10:
            target_md = date_str[5:]  # MM-DD
        else:
            target_md = datetime.fromisoformat(date_str).strftime("%m-%d")
    except Exception:
        return None

    from solo.core.workspace import get_user_path

    user_path = get_user_path(workspace)
    if not user_path.exists():
        return None

    text = user_path.read_text(encoding="utf-8", errors="replace")

    # Find the ## Important dates section
    section_match = re.search(r"^##\s+Important dates\b", text, re.MULTILINE)
    if not section_match:
        return None
    section_text = text[section_match.end():]
    # Truncate at the next heading
    next_heading = re.search(r"^##", section_text, re.MULTILINE)
    if next_heading:
        section_text = section_text[: next_heading.start()]

    # Pattern: `- Label: MM-DD` or `- Label: YYYY-MM-DD`
    pattern = re.compile(
        r"^\s*-\s+(.+?):\s+(?:\d{4}-)?(\d{2}-\d{2})\s*$", re.MULTILINE
    )
    hits: list[str] = []
    for m in pattern.finditer(section_text):
        label = m.group(1).strip()
        if m.group(2) == target_md:
            hits.append(label)

    return ", ".join(hits) if hits else None


def _previous_day(process_date: str | None) -> str:
    """Get the ISO date string of the day before process_date."""
    if process_date:
        return (datetime.fromisoformat(process_date).date() - timedelta(days=1)).isoformat()
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()
