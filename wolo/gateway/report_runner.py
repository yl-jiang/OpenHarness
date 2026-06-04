#!/usr/bin/env python3
"""Cron runner script for wolo weekly/monthly reports."""

from __future__ import annotations

import argparse
import asyncio
import calendar
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))


def _parse_utc_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_due_local_time(
    *,
    job_name: str | None,
    workspace: str | None,
    fallback_timezone: str,
) -> tuple[datetime, str]:
    from wolo.gateway.report_cron import _get_cron_job

    timezone_name = fallback_timezone
    if job_name:
        job = _get_cron_job(job_name, workspace)
        if job is not None:
            timezone_name = str(job.get("timezone") or job.get("tz") or fallback_timezone).strip() or fallback_timezone
            due_utc = _parse_utc_datetime(job.get("next_run"))
            if due_utc is not None:
                return due_utc.astimezone(ZoneInfo(timezone_name)), timezone_name
    return datetime.now(ZoneInfo(timezone_name)), timezone_name


def _resolve_report_period(report_type: str, due_local: datetime) -> tuple[str, str] | None:
    anchor_date = due_local.date()
    if report_type == "weekly":
        week_end = anchor_date - timedelta(days=(anchor_date.weekday() - 6) % 7)
        week_start = week_end - timedelta(days=6)
        return week_start.isoformat(), week_end.isoformat()
    if report_type == "monthly":
        last_day = calendar.monthrange(anchor_date.year, anchor_date.month)[1]
        if anchor_date.day != last_day:
            return None
        month_start = anchor_date.replace(day=1)
        return month_start.isoformat(), anchor_date.isoformat()
    raise ValueError(f"unsupported report type: {report_type}")


async def _push_report_to_im(workspace: str | None, content: str) -> None:
    from wolo.gateway.feed_digest_runner import _push_to_im

    await _push_to_im(workspace, content)


async def _main(
    app: str,
    report_type: str,
    workspace: str | None,
    job_name: str | None,
    timezone_name: str,
    push: bool = True,
) -> int:
    from openharness.utils.log import configure_logging, get_logger

    configure_logging(level="INFO")
    logger = get_logger(__name__)

    try:
        from wolo.agent import OpenHarnessWoloAgent
        from wolo.config import load_config
        from wolo.core.store import WoloStore
        from wolo.processor import WoloProcessor

        due_local, resolved_timezone = _resolve_due_local_time(
            job_name=job_name,
            workspace=workspace,
            fallback_timezone=timezone_name,
        )
        period = _resolve_report_period(report_type, due_local)
        if period is None:
            print(
                "SKIPPED: monthly report only runs on the month's last local day "
                f"(due_local_date={due_local.date().isoformat()} tz={resolved_timezone})"
            )
            return 0
        start_date, end_date = period

        store = WoloStore(workspace)
        profile = load_config(workspace).provider_profile
        agent = OpenHarnessWoloAgent(
            profile=profile,
            record_model_call=store.record_llm_call,
        )
        report = await WoloProcessor(store, agent).generate_report(
            report_type,
            start_date=start_date,
            end_date=end_date,
        )
        if push and report.content:
            await _push_report_to_im(workspace, report.content)
        logger.info(
            "Report complete app=%s type=%s id=%s period=%s~%s tz=%s",
            app,
            report_type,
            report.id,
            start_date,
            end_date,
            resolved_timezone,
        )
        print(
            f"Report done: app={app} type={report.report_type} id={report.id} "
            f"period={start_date}~{end_date} tz={resolved_timezone}"
        )
        return 0
    except Exception as exc:
        logger.error("Report generation failed: %s", exc, exc_info=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", default="wolo")
    parser.add_argument("--report-type", required=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--job-name", default=None)
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--no-push", dest="push", action="store_false")
    parser.set_defaults(push=True)
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            _main(
                args.app,
                args.report_type,
                args.workspace,
                args.job_name,
                args.timezone,
                args.push,
            )
        )
    )
