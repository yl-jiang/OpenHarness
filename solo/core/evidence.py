"""Evidence pack builders for insight reports.

Pre-computed cross-tabulations and anomaly detection — the quality ceiling
for what the LLM can discover.  No LLM calls here; pure Python statistics.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from solo.core.store import SoloStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _z_score(value: float, mean: float, std: float) -> float:
    """Compute z-score; returns 0 if std is 0."""
    return (value - mean) / std if std else 0.0


def _compute_prev_period(
    start_date: str, end_date: str,
) -> tuple[str, str]:
    """Compute the previous period boundaries by shifting the current span backward.

    For any period [start, end], returns [start - span, start - 1] where
    span = (end - start + 1) days.  This works for weekly, monthly, or
    arbitrary periods without needing report_type.
    """
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    span = (end_dt - start_dt).days + 1
    prev_end = (start_dt - timedelta(days=1)).strftime("%Y-%m-%d")
    prev_start = (start_dt - timedelta(days=span)).strftime("%Y-%m-%d")
    return prev_start, prev_end


_WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Finance Evidence
# ---------------------------------------------------------------------------

def build_finance_evidence(
    store: SoloStore,
    *,
    start_date: str,
    end_date: str,
    prev_start: str | None = None,
    prev_end: str | None = None,
) -> dict[str, Any]:
    """Build pre-computed finance statistics for the LLM insight generator."""
    # Current period transactions
    txns = store.list_finance_transactions(date_from=start_date, date_to=end_date)
    expenses = [t for t in txns if t.type == "expense"]
    incomes = [t for t in txns if t.type == "income"]

    total_expense = sum(t.amount for t in expenses)
    total_income = sum(t.amount for t in incomes)

    # Date range span
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    days = (end_dt - start_dt).days + 1
    daily_avg = total_expense / days if days else 0

    # Day-of-week distribution
    dow_sums: dict[int, float] = {}
    dow_counts: dict[int, int] = {}
    for t in expenses:
        try:
            wd = datetime.strptime(t.date, "%Y-%m-%d").weekday()
        except (ValueError, TypeError):
            continue
        dow_sums[wd] = dow_sums.get(wd, 0) + t.amount
        dow_counts[wd] = dow_counts.get(wd, 0) + 1

    dow_distribution: dict[str, Any] = {}
    weekday_totals: list[float] = []
    for i in range(7):
        count = dow_counts.get(i, 0)
        avg = dow_sums.get(i, 0) / count if count > 0 else 0.0
        dow_distribution[_WEEKDAY_NAMES[i]] = round(avg, 1)
        if i < 5:
            weekday_totals.append(avg)

    peak_day = max(dow_distribution, key=lambda k: dow_distribution[k]) if dow_distribution else ""
    weekday_avg = sum(weekday_totals) / len(weekday_totals) if weekday_totals else 0
    dow_distribution["peak_day"] = peak_day
    dow_distribution["peak_avg"] = dow_distribution.get(peak_day, 0)
    dow_distribution["weekday_avg"] = round(weekday_avg, 1)

    # Category breakdown
    cat_sums: dict[str, float] = Counter()
    for t in expenses:
        if t.category:
            cat_sums[t.category] += t.amount

    # Previous period comparison for category shift
    category_shift: list[dict[str, Any]] = []
    if prev_start and prev_end:
        prev_txns = store.list_finance_transactions(date_from=prev_start, date_to=prev_end)
        prev_expenses = [t for t in prev_txns if t.type == "expense"]
        prev_cat_sums: dict[str, float] = Counter()
        for t in prev_expenses:
            if t.category:
                prev_cat_sums[t.category] += t.amount
        prev_total = sum(prev_cat_sums.values()) or 1
        cur_total = sum(cat_sums.values()) or 1
        all_cats = set(cat_sums) | set(prev_cat_sums)
        for cat in sorted(all_cats):
            cur_pct = round(cat_sums.get(cat, 0) / cur_total * 100, 1)
            prev_pct = round(prev_cat_sums.get(cat, 0) / prev_total * 100, 1)
            delta = round(cur_pct - prev_pct, 1)
            if abs(delta) > 10:
                category_shift.append({
                    "category": cat,
                    "current_pct": cur_pct,
                    "prev_pct": prev_pct,
                    "delta": delta,
                })

    # Frequent merchants
    counterparty_counts: dict[str, dict[str, Any]] = {}
    for t in expenses:
        if t.counterparty:
            if t.counterparty not in counterparty_counts:
                counterparty_counts[t.counterparty] = {"count": 0, "total": 0.0}
            counterparty_counts[t.counterparty]["count"] += 1
            counterparty_counts[t.counterparty]["total"] += t.amount
    frequent_merchants = [
        {"counterparty": k, "count": v["count"], "total": round(v["total"], 1)}
        for k, v in sorted(counterparty_counts.items(), key=lambda x: x[1]["count"], reverse=True)
        if v["count"] >= 3
    ]

    # Subscription detection (same amount + same counterparty across months)
    subscription_candidates: dict[str, dict[str, Any]] = {}
    for t in expenses:
        key = f"{t.counterparty}|{t.amount}"
        if not t.counterparty:
            continue
        if key not in subscription_candidates:
            subscription_candidates[key] = {"counterparty": t.counterparty, "amount": t.amount, "months": set()}
        try:
            month = t.date[:7]
            subscription_candidates[key]["months"].add(month)
        except (TypeError, IndexError):
            pass
    subscriptions = [
        {"counterparty": v["counterparty"], "amount": v["amount"], "months": len(v["months"])}
        for v in subscription_candidates.values()
        if len(v["months"]) >= 2
    ]

    # Budget breaches
    budgets = store.list_finance_budgets(active=True)
    budget_breaches: list[dict[str, Any]] = []
    for b in budgets:
        if not b.category:
            continue
        spent = cat_sums.get(b.category, 0)
        utilization = spent / b.amount if b.amount else 0
        if utilization > 0.8:
            budget_breaches.append({
                "category": b.category,
                "budget": b.amount,
                "spent": round(spent, 1),
                "utilization": round(utilization, 2),
            })

    # Single-transaction anomalies (amount > mean + 2σ)
    anomalies: list[dict[str, Any]] = []
    if expenses:
        amounts = [t.amount for t in expenses]
        mean_amt = sum(amounts) / len(amounts)
        std_amt = math.sqrt(sum((a - mean_amt) ** 2 for a in amounts) / len(amounts)) if len(amounts) > 1 else 0
        for t in expenses:
            z = _z_score(t.amount, mean_amt, std_amt)
            if z > 2:
                anomalies.append({
                    "date": t.date,
                    "amount": t.amount,
                    "mean": round(mean_amt, 1),
                    "z_score": round(z, 1),
                })

    # Income change
    income_change: dict[str, Any] = {}
    if prev_start and prev_end:
        prev_txns_all = store.list_finance_transactions(date_from=prev_start, date_to=prev_end)
        prev_income = sum(t.amount for t in prev_txns_all if t.type == "income")
        if prev_income:
            delta_pct = round((total_income - prev_income) / prev_income * 100, 1)
            income_change = {
                "current": total_income,
                "previous": prev_income,
                "delta_pct": delta_pct,
            }

    result: dict[str, Any] = {
        "period": {"start": start_date, "end": end_date},
        "record_count": len(txns),
        "total_expense": round(total_expense, 1),
        "total_income": round(total_income, 1),
        "daily_avg_expense": round(daily_avg, 1),
        "day_of_week_distribution": dow_distribution,
        "category_shift": category_shift,
        "frequent_merchants": frequent_merchants,
        "subscriptions": subscriptions,
        "budget_breaches": budget_breaches,
        "anomalies": anomalies,
        "income_change": income_change,
    }
    if prev_start and prev_end:
        result["prev_period"] = {"start": prev_start, "end": prev_end}

    return result


# ---------------------------------------------------------------------------
# Health Evidence
# ---------------------------------------------------------------------------

def build_health_evidence(
    store: SoloStore,
    *,
    start_date: str,
    end_date: str,
    prev_start: str | None = None,
    prev_end: str | None = None,
) -> dict[str, Any]:
    """Build pre-computed health statistics for the LLM insight generator.

    Includes previous-period comparison for sleep and exercise when
    prev_start/prev_end are provided.
    """
    records = store.list_health_records(date_from=start_date, date_to=end_date)

    # --- Sleep ---
    sleep_records = [r for r in records if r.sleep_hours > 0]
    sleep_hours_list = [r.sleep_hours for r in sleep_records]
    sleep_mean = sum(sleep_hours_list) / len(sleep_hours_list) if sleep_hours_list else 0
    sleep_std = math.sqrt(
        sum((h - sleep_mean) ** 2 for h in sleep_hours_list) / len(sleep_hours_list)
    ) if len(sleep_hours_list) > 1 else 0
    # 7-day rolling average trend
    sorted_sleep = sorted(sleep_records, key=lambda r: r.date)
    sleep_trend = [round(r.sleep_hours, 1) for r in sorted_sleep[-7:]]
    low_sleep_days = sum(1 for h in sleep_hours_list if h < sleep_mean - sleep_std) if sleep_std else 0

    # --- Sleep ↔ Mood correlation (next-day) ---
    # Map date → mood_sentiment (preferred) and mood (fallback) for next-day lookup
    date_mood_sentiment: dict[str, str] = {}
    for r in records:
        if r.mood_sentiment:
            date_mood_sentiment[r.date] = r.mood_sentiment
    low_sleep_negative_mood = 0
    low_sleep_total = 0
    normal_sleep_negative_mood = 0
    normal_sleep_total = 0
    for r in sleep_records:
        # Look up mood on the NEXT day after this sleep record
        try:
            next_day = (datetime.strptime(r.date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            continue
        next_sentiment = date_mood_sentiment.get(next_day)
        # Only classify via mood_sentiment (LLM-classified at write time).
        # Records without mood_sentiment are excluded from the correlation.
        if not next_sentiment:
            continue
        is_negative = next_sentiment == "negative"
        is_low_sleep = r.sleep_hours < sleep_mean - sleep_std and sleep_std > 0
        if is_low_sleep:
            low_sleep_total += 1
            if is_negative:
                low_sleep_negative_mood += 1
        else:
            normal_sleep_total += 1
            if is_negative:
                normal_sleep_negative_mood += 1

    sleep_mood_correlation: dict[str, Any] = {}
    if low_sleep_total:
        sleep_mood_correlation["low_sleep_negative_mood_rate"] = round(low_sleep_negative_mood / low_sleep_total, 2)
    if normal_sleep_total:
        sleep_mood_correlation["normal_sleep_negative_mood_rate"] = round(normal_sleep_negative_mood / normal_sleep_total, 2)

    # --- Exercise ---
    exercise_records = [r for r in records if r.exercise_type or r.category == "exercise" or r.category == "fitness"]
    exercise_dates = sorted(set(r.date for r in exercise_records))
    days_with_exercise = len(exercise_dates)

    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    total_days = (end_dt - start_dt).days + 1

    # Max gap between exercise days
    max_gap = 0
    avg_gap = 0.0
    if len(exercise_dates) >= 2:
        gaps = []
        for i in range(1, len(exercise_dates)):
            try:
                d1 = datetime.strptime(exercise_dates[i - 1], "%Y-%m-%d")
                d2 = datetime.strptime(exercise_dates[i], "%Y-%m-%d")
                gap = (d2 - d1).days
                gaps.append(gap)
            except ValueError:
                continue
        if gaps:
            max_gap = max(gaps)
            avg_gap = round(sum(gaps) / len(gaps), 1)
    elif exercise_dates:
        # Only one exercise day — gap from start to that day
        try:
            d = datetime.strptime(exercise_dates[0], "%Y-%m-%d")
            max_gap = (d - start_dt).days
            avg_gap = float(max_gap)
        except ValueError:
            pass

    # --- Medication adherence ---
    med_records = [r for r in records if r.category == "medication"]
    med_dates = set(r.date for r in med_records)
    missed_days_of_week: list[str] = []
    day_coverage = {i: False for i in range(7)}
    for d_str in med_dates:
        try:
            wd = datetime.strptime(d_str, "%Y-%m-%d").weekday()
            day_coverage[wd] = True
        except ValueError:
            continue
    for wd, covered in day_coverage.items():
        if not covered:
            missed_days_of_week.append(_WEEKDAY_NAMES[wd])
    adherence_rate = len(med_dates) / total_days if total_days else 0

    # --- Symptom recurrence ---
    symptom_records = [r for r in records if r.category == "symptom" and r.item]
    symptom_occurrences: dict[str, list[str]] = {}
    for r in symptom_records:
        symptom_occurrences.setdefault(r.item, []).append(r.date)
    symptom_recurrence = [
        {"item": item, "occurrences": len(dates), "dates": [d[5:] for d in sorted(dates)]}
        for item, dates in sorted(symptom_occurrences.items())
        if len(dates) > 2
    ]

    # --- Vitals trend ---
    vital_records = [r for r in records if r.category == "vital"]
    hr_trend: list[int] = []
    spo2_anomalies: list[dict[str, Any]] = []
    for r in sorted(vital_records, key=lambda r: r.date):
        metrics = r.metrics
        if "heart_rate" in metrics or "resting_hr" in metrics:
            hr = metrics.get("heart_rate") or metrics.get("resting_hr", 0)
            if isinstance(hr, (int, float)):
                hr_trend.append(int(hr))
        if "spo2" in metrics or "blood_oxygen" in metrics:
            spo2 = metrics.get("spo2") or metrics.get("blood_oxygen", 0)
            if isinstance(spo2, (int, float)) and spo2 < 95:
                spo2_anomalies.append({"date": r.date, "value": spo2})

    hr_delta = ""
    if len(hr_trend) >= 2:
        delta = hr_trend[-1] - hr_trend[0]
        hr_delta = f"+{delta} bpm" if delta > 0 else f"{delta} bpm"

    # --- Stress ↔ Exercise ---
    stress_records = [r for r in records if r.stress_level]
    high_stress = [r for r in stress_records if r.stress_level in ("high", "very_high", "7", "8", "9", "10")]
    low_stress = [r for r in stress_records if r.stress_level in ("low", "very_low", "1", "2", "3")]
    # Count exercise days in high-stress vs low-stress weeks (simplified: per-record)
    high_stress_exercise_avg = 0.0
    low_stress_exercise_avg = 0.0
    if high_stress:
        high_stress_dates = set(r.date for r in high_stress)
        high_stress_exercise_count = sum(1 for d in exercise_dates if d in high_stress_dates)
        high_stress_exercise_avg = round(high_stress_exercise_count / len(high_stress_dates), 1) if high_stress_dates else 0
    if low_stress:
        low_stress_dates = set(r.date for r in low_stress)
        low_stress_exercise_count = sum(1 for d in exercise_dates if d in low_stress_dates)
        low_stress_exercise_avg = round(low_stress_exercise_count / len(low_stress_dates), 1) if low_stress_dates else 0

    # --- Previous period comparison ---
    prev_sleep_comparison: dict[str, Any] = {}
    prev_exercise_comparison: dict[str, Any] = {}
    if prev_start and prev_end:
        prev_records = store.list_health_records(date_from=prev_start, date_to=prev_end)
        prev_sleep = [r for r in prev_records if r.sleep_hours > 0]
        if prev_sleep:
            prev_sleep_mean = round(sum(r.sleep_hours for r in prev_sleep) / len(prev_sleep), 1)
            prev_sleep_comparison = {
                "prev_mean": prev_sleep_mean,
                "delta": round(sleep_mean - prev_sleep_mean, 1),
                "delta_pct": round((sleep_mean - prev_sleep_mean) / prev_sleep_mean * 100, 1) if prev_sleep_mean else 0,
            }
        prev_exercise = [r for r in prev_records if r.exercise_type or r.category == "exercise" or r.category == "fitness"]
        prev_exercise_days = len(set(r.date for r in prev_exercise))
        if prev_exercise_days:
            prev_exercise_comparison = {
                "prev_days_with_exercise": prev_exercise_days,
                "delta": days_with_exercise - prev_exercise_days,
            }

    result: dict[str, Any] = {
        "period": {"start": start_date, "end": end_date},
        "record_count": len(records),
        "sleep": {
            "mean": round(sleep_mean, 1),
            "std": round(sleep_std, 1),
            "trend": sleep_trend,
            "low_sleep_days": low_sleep_days,
            "prev_comparison": prev_sleep_comparison,
        },
        "sleep_mood_correlation": sleep_mood_correlation,
        "exercise": {
            "days_with_exercise": days_with_exercise,
            "total_days": total_days,
            "max_gap_days": max_gap,
            "avg_gap_days": avg_gap,
            "prev_comparison": prev_exercise_comparison,
        },
        "medication_adherence": {
            "expected_days": total_days,
            "actual_days": len(med_dates),
            "missed_days_of_week": missed_days_of_week,
            "adherence_rate": round(adherence_rate, 2),
        },
        "symptom_recurrence": symptom_recurrence,
        "vitals": {
            "resting_hr_trend": hr_trend,
            "hr_delta": hr_delta,
            "spo2_anomalies": spo2_anomalies,
        },
        "stress_exercise": {
            "high_stress_weeks_exercise_avg": high_stress_exercise_avg,
            "low_stress_weeks_exercise_avg": low_stress_exercise_avg,
        },
    }
    if prev_start and prev_end:
        result["prev_period"] = {"start": prev_start, "end": prev_end}

    return result
