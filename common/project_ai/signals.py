"""Project State Analysis: generate signals, snapshots, and checkin questions.

Two strategies:
  1. Deterministic rules: check activity, target dates, milestones, blockers.
  2. LLM fallback (when agent is available): richer semantic analysis.

Output: signals, snapshots, and checkin questions ready for the store.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

logger = logging.getLogger(__name__)

# Stale threshold: no activity for N days
STALE_DAYS = 14
# Target proximity: target date within N days
TARGET_PROXIMITY_DAYS = 7
# Low completion threshold for attention
LOW_COMPLETION_PCT = 80


class StateStore(Protocol):
    """Minimal store interface for state analysis."""

    def list_projects(self, **kwargs: Any) -> list[Any]: ...
    def get_project_detail(self, project_id: str) -> dict | None: ...
    def list_project_signals(self, project_id: str, **kwargs: Any) -> list[Any]: ...
    def get_latest_project_snapshot(self, project_id: str) -> Any | None: ...
    def list_milestones(self, project_id: str) -> list[Any]: ...
    def get_recent_checkin_question(self, project_id: str, *, days: int = 7) -> str | None: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(s: str) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Deterministic signal generation
# ---------------------------------------------------------------------------

def _analyze_project(detail: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate signals for a single project using deterministic rules."""
    signals: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    project_id = detail.get("id", "")

    risk_status = detail.get("risk_status", "normal")
    activity_7d = detail.get("activity_7d", 0)
    activity_30d = detail.get("activity_30d", 0)
    completion_pct = detail.get("completion_pct")
    milestone_count = detail.get("milestone_count", 0)
    completed_milestone_count = detail.get("completed_milestone_count", 0)
    open_blocker_count = detail.get("open_blocker_count", 0)
    last_activity_at = detail.get("last_activity_at", "")

    # 1. Stale detection
    if last_activity_at:
        last_dt = _parse_dt(last_activity_at)
        if last_dt and (now - last_dt).days >= STALE_DAYS:
            signals.append({
                "project_id": project_id,
                "signal_type": "stale",
                "summary": f"No activity for {(now - last_dt).days} days",
                "severity": "warning" if (now - last_dt).days < 30 else "critical",
            })
    elif not last_activity_at and detail.get("created_at"):
        created_dt = _parse_dt(detail["created_at"])
        if created_dt and (now - created_dt).days >= STALE_DAYS:
            signals.append({
                "project_id": project_id,
                "signal_type": "stale",
                "summary": f"No activity since creation ({(now - created_dt).days} days ago)",
                "severity": "warning",
            })

    # 2. Target date risk
    target_date = detail.get("target_date", "")
    if target_date and detail.get("status") != "completed":
        target_dt = _parse_dt(target_date)
        if target_dt:
            days_left = (target_dt.date() - now.date()).days
            if days_left < 0:
                signals.append({
                    "project_id": project_id,
                    "signal_type": "risk",
                    "summary": f"Target date overdue by {abs(days_left)} days",
                    "severity": "critical",
                })
            elif days_left <= TARGET_PROXIMITY_DAYS:
                pct = completion_pct or 0
                if pct < LOW_COMPLETION_PCT:
                    signals.append({
                        "project_id": project_id,
                        "signal_type": "risk",
                        "summary": (
                            f"Target in {days_left} days but only "
                            f"{pct}% complete"
                        ),
                        "severity": "warning",
                    })

    # 3. Momentum: high recent activity
    if activity_7d >= 5:
        signals.append({
            "project_id": project_id,
            "signal_type": "momentum",
            "summary": f"Strong momentum: {activity_7d} activities in the last 7 days",
            "severity": "info",
        })

    # 4. Progress: milestone completion
    if completed_milestone_count > 0 and milestone_count > 0:
        pct_ms = int(round(completed_milestone_count / milestone_count * 100))
        signals.append({
            "project_id": project_id,
            "signal_type": "progress",
            "summary": (
                f"{completed_milestone_count}/{milestone_count} milestones "
                f"completed ({pct_ms}%)"
            ),
            "severity": "info",
        })

    # 5. Blockers
    if open_blocker_count > 0:
        signals.append({
            "project_id": project_id,
            "signal_type": "blocker",
            "summary": f"{open_blocker_count} open blocker(s) need resolution",
            "severity": "warning" if open_blocker_count == 1 else "critical",
        })

    # 6. Completion signal
    if completion_pct is not None and completion_pct >= 100 and detail.get("status") != "completed":
        signals.append({
            "project_id": project_id,
            "signal_type": "milestone_evidence",
            "summary": "All tracked items complete — consider marking project as completed",
            "severity": "info",
        })

    return signals


def _suggest_next_action(detail: dict[str, Any]) -> str:
    """Suggest a concrete next action based on project state."""
    risk_status = detail.get("risk_status", "normal")
    open_blocker_count = detail.get("open_blocker_count", 0)
    completion_pct = detail.get("completion_pct")
    activity_7d = detail.get("activity_7d", 0)
    milestone_count = detail.get("milestone_count", 0)
    completed_milestone_count = detail.get("completed_milestone_count", 0)
    last_activity_at = detail.get("last_activity_at", "")

    if open_blocker_count > 0:
        return "Resolve open blockers before continuing"

    now = datetime.now(timezone.utc)
    if last_activity_at:
        last_dt = _parse_dt(last_activity_at)
        if last_dt and (now - last_dt).days >= STALE_DAYS:
            return "Review whether to resume, pause, or archive this project"

    if risk_status == "at_risk":
        return "Reassess target date and remaining scope"
    if risk_status == "attention":
        return "Identify the smallest next step to keep momentum"

    if completion_pct is not None and completion_pct >= 100:
        return "Mark project as completed and write a brief review"

    if milestone_count > 0 and completed_milestone_count < milestone_count:
        return "Work on the next pending milestone"

    if activity_7d == 0:
        return "Log today's progress or review recent records"

    return "Continue current work and log progress"


# ---------------------------------------------------------------------------
# LLM-based analysis
# ---------------------------------------------------------------------------

async def _llm_analyze(
    *,
    detail: dict[str, Any],
    agent: Any,
) -> dict[str, Any] | None:
    """Use LLM to analyze a project's state. Returns parsed JSON or None."""
    from common.project_ai.prompts import PROJECT_STATE_SYSTEM_PROMPT

    # Build compact context
    title = detail.get("title", "")
    status = detail.get("status", "")
    risk = detail.get("risk_status", "normal")
    pct = detail.get("completion_pct")
    ms = f"{detail.get('completed_milestone_count', 0)}/{detail.get('milestone_count', 0)}"
    a7 = detail.get("activity_7d", 0)
    a30 = detail.get("activity_30d", 0)
    blockers = detail.get("open_blocker_count", 0)
    last = detail.get("last_activity_at", "never")
    target = detail.get("target_date", "none")

    user_msg = (
        f"## Project: {title}\n"
        f"- Status: {status}, Risk: {risk}\n"
        f"- Completion: {pct}%\n"
        f"- Milestones: {ms}\n"
        f"- Activity: {a7} (7d), {a30} (30d)\n"
        f"- Open blockers: {blockers}\n"
        f"- Last activity: {last}\n"
        f"- Target date: {target}\n\n"
        "Analyze project health and generate signals. Return JSON."
    )

    try:
        raw = await agent.run_prompt(PROJECT_STATE_SYSTEM_PROMPT, user_msg)
        return json.loads(raw)
    except Exception:
        logger.warning("LLM state analysis failed for %s", title, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def analyze_project_state(
    *,
    store: StateStore,
    project_id: str,
    agent: Any | None = None,
) -> dict[str, Any]:
    """Analyze a single project and return signals + next_action + summary.

    Always runs deterministic rules. If agent is provided, enriches with LLM.

    Returns
    -------
    dict with keys: signals, next_action, summary
    """
    detail = store.get_project_detail(project_id)
    if detail is None:
        return {"signals": [], "next_action": "", "summary": ""}

    # Deterministic signals
    det_signals = _analyze_project(detail)
    next_action = _suggest_next_action(detail)

    # LLM enrichment
    if agent is not None:
        llm_result = await _llm_analyze(detail=detail, agent=agent)
        if llm_result:
            llm_signals = llm_result.get("signals", [])
            # Merge: LLM signals supplement deterministic ones
            # Deduplicate by (signal_type, summary)
            seen = {(s["signal_type"], s["summary"]) for s in det_signals}
            for ls in llm_signals:
                key = (ls.get("signal_type", ""), ls.get("summary", ""))
                if key not in seen:
                    ls["project_id"] = project_id
                    det_signals.append(ls)
                    seen.add(key)
            # Prefer LLM next_action and summary if available
            if llm_result.get("next_action"):
                next_action = llm_result["next_action"]
            summary = llm_result.get("summary", "")
        else:
            summary = _deterministic_summary(detail)
    else:
        summary = _deterministic_summary(detail)

    return {
        "signals": det_signals[:8],
        "next_action": next_action,
        "summary": summary,
    }


def _deterministic_summary(detail: dict[str, Any]) -> str:
    """Generate a brief summary from deterministic state."""
    title = detail.get("title", "Project")
    risk = detail.get("risk_status", "normal")
    pct = detail.get("completion_pct")
    a7 = detail.get("activity_7d", 0)
    blockers = detail.get("open_blocker_count", 0)

    parts: list[str] = []

    if risk == "at_risk":
        parts.append(f"{title} is at risk.")
    elif risk == "attention":
        parts.append(f"{title} needs attention.")
    else:
        parts.append(f"{title} is on track.")

    if pct is not None:
        parts.append(f"Progress: {pct}%.")

    if a7 > 0:
        parts.append(f"{a7} activities this week.")
    else:
        parts.append("No recent activity.")

    if blockers > 0:
        parts.append(f"{blockers} blocker(s) open.")

    return " ".join(parts)


async def generate_daily_snapshot(
    *,
    store: StateStore,
    project_id: str,
    agent: Any | None = None,
) -> dict[str, Any]:
    """Generate a daily snapshot for a project.

    Returns a dict ready for creating a ProjectSnapshot.
    """
    result = await analyze_project_state(store=store, project_id=project_id, agent=agent)
    detail = store.get_project_detail(project_id)
    if detail is None:
        return {}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    return {
        "project_id": project_id,
        "snapshot_date": today,
        "summary": result.get("summary", ""),
        "health": detail.get("risk_status", "normal"),
        "completion_pct": detail.get("completion_pct"),
        "activity_7d": detail.get("activity_7d", 0),
        "open_blocker_count": detail.get("open_blocker_count", 0),
        "next_action": result.get("next_action", ""),
    }


async def generate_checkin_questions(
    *,
    store: StateStore,
    agent: Any | None = None,
    app_type: str = "wolo",
    max_questions: int = 3,
) -> list[dict[str, str]]:
    """Generate project checkin questions for active projects.

    Returns list of dicts with: project_id, project_title, question, reason.
    """
    projects = store.list_projects(status="active")
    if not projects:
        return []

    # Gather project contexts, prioritizing at-risk and stale
    contexts: list[dict[str, Any]] = []
    for p in projects:
        d = p.to_dict() if hasattr(p, "to_dict") else p
        pid = d.get("id", "")
        detail = store.get_project_detail(pid)
        if detail is None:
            continue
        recent_q = store.get_recent_checkin_question(pid)
        contexts.append({
            "id": pid,
            "title": d.get("title", ""),
            "risk_status": detail.get("risk_status", "normal"),
            "completion_pct": detail.get("completion_pct"),
            "activity_7d": detail.get("activity_7d", 0),
            "open_blocker_count": detail.get("open_blocker_count", 0),
            "last_activity_at": detail.get("last_activity_at", ""),
            "recent_checkin": recent_q or "",
        })

    # Sort: at_risk first, then attention, then normal
    risk_order = {"at_risk": 0, "attention": 1, "normal": 2}
    contexts.sort(key=lambda c: risk_order.get(c["risk_status"], 2))

    if agent is not None:
        return await _llm_checkin(contexts, agent=agent, app_type=app_type, max_questions=max_questions)

    return _deterministic_checkin(contexts, app_type=app_type, max_questions=max_questions)


def _deterministic_checkin(
    contexts: list[dict[str, Any]],
    *,
    app_type: str = "wolo",
    max_questions: int = 3,
) -> list[dict[str, str]]:
    """Generate checkin questions from deterministic rules."""
    questions: list[dict[str, str]] = []

    for ctx in contexts:
        if len(questions) >= max_questions:
            break

        title = ctx["title"]
        risk = ctx["risk_status"]
        a7 = ctx["activity_7d"]
        blockers = ctx["open_blocker_count"]
        pct = ctx.get("completion_pct")
        recent_q = ctx.get("recent_checkin", "")

        q = ""
        if blockers > 0:
            q = f"「{title}」has {blockers} open blocker(s). What's the next step to unblock?"
        elif risk == "at_risk":
            q = f"「{title}」is at risk. Should we adjust the target or reduce scope?"
        elif a7 == 0 and risk != "normal":
            q = f"「{title}」has no activity this week. Should we pause it or set a smaller next step?"
        elif pct is not None and pct >= 90:
            q = f"「{title}」is {pct}% done. Is it ready to be marked as completed?"
        elif risk == "attention":
            q = f"「{title}」needs attention. What's the smallest action you can take today?"
        elif a7 >= 5:
            if app_type == "solo":
                q = f"You've been active on「{title}」this week. How is it feeling — energizing or draining?"
            else:
                q = f"「{title}」has strong momentum. Any milestone ready to close?"

        if q and q != recent_q:
            questions.append({
                "project_id": ctx["id"],
                "project_title": title,
                "question": q,
                "reason": f"risk={risk}, activity_7d={a7}",
            })

    return questions


async def _llm_checkin(
    contexts: list[dict[str, Any]],
    *,
    agent: Any,
    app_type: str = "wolo",
    max_questions: int = 3,
) -> list[dict[str, str]]:
    """Use LLM to generate checkin questions."""
    from common.project_ai.prompts import PROJECT_CHECKIN_SYSTEM_PROMPT

    tone = "solo" if app_type == "solo" else "wolo"
    projects_text = "\n".join(
        f"- [{c['id']}] {c['title']}: risk={c['risk_status']}, "
        f"activity_7d={c['activity_7d']}, blockers={c['open_blocker_count']}, "
        f"completion={c.get('completion_pct', 'N/A')}%, "
        f"recent_checkin={c.get('recent_checkin', '(none)')}"
        for c in contexts[:10]
    )
    user_msg = (
        f"## Active projects\n{projects_text}\n\n"
        f"App type: {tone}\n"
        f"Generate up to {max_questions} check-in questions. Return JSON."
    )

    try:
        raw = await agent.run_prompt(PROJECT_CHECKIN_SYSTEM_PROMPT, user_msg)
        data = json.loads(raw)
    except Exception:
        logger.warning("LLM checkin generation failed", exc_info=True)
        return _deterministic_checkin(contexts, app_type=app_type, max_questions=max_questions)

    results: list[dict[str, str]] = []
    for q in data.get("questions", [])[:max_questions]:
        if q.get("question"):
            results.append({
                "project_id": q.get("project_id", ""),
                "project_title": q.get("project_title", ""),
                "question": q["question"],
                "reason": q.get("reason", ""),
            })

    return results or _deterministic_checkin(contexts, app_type=app_type, max_questions=max_questions)
