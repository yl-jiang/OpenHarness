from __future__ import annotations

import asyncio
import json
from pathlib import Path

from solo.core.store import SoloStore
from solo.core.workspace import ensure_workspace, get_data_dir


def _update_job_next_run(workspace: Path, job_name: str, next_run: str, *, timezone_name: str | None = None) -> None:
    jobs_path = get_data_dir(workspace) / "cron_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    for job in jobs:
        if job["name"] != job_name:
            continue
        job["next_run"] = next_run
        if timezone_name is not None:
            job["timezone"] = timezone_name
        break
    jobs_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_solo_report_cron_job_registration_and_idempotent(tmp_path: Path) -> None:
    from solo.gateway.report_cron import ensure_report_jobs

    workspace = ensure_workspace(tmp_path / ".solo")
    ensure_report_jobs("solo", workspace=workspace)
    ensure_report_jobs("solo", workspace=workspace)

    cron_path = get_data_dir(workspace) / "cron_jobs.json"
    jobs = json.loads(cron_path.read_text(encoding="utf-8"))
    weekly = [job for job in jobs if job["name"] == "solo-weekly-report"]
    monthly = [job for job in jobs if job["name"] == "solo-monthly-report"]

    assert len(weekly) == 1
    assert len(monthly) == 1
    assert weekly[0]["schedule"] == "0 21 * * 0"
    assert monthly[0]["schedule"] == "0 21 28-31 * *"
    assert weekly[0]["timezone"] == "Asia/Shanghai"
    assert monthly[0]["timezone"] == "Asia/Shanghai"
    assert weekly[0]["timeout_s"] == 900
    assert monthly[0]["timeout_s"] == 900
    assert "report_runner.py" in weekly[0]["command"]
    assert "--report-type weekly" in weekly[0]["command"]
    assert "--job-name solo-weekly-report" in weekly[0]["command"]
    assert "--report-type monthly" in monthly[0]["command"]


def test_solo_report_cron_reconciles_existing_drift(tmp_path: Path) -> None:
    from solo.gateway import report_cron

    workspace = ensure_workspace(tmp_path / ".solo")
    jobs_path = get_data_dir(workspace) / "cron_jobs.json"
    jobs_path.write_text(
        json.dumps(
            [
                {
                    "name": "solo-weekly-report",
                    "schedule": "5 * * * *",
                    "timezone": "UTC",
                    "command": "/tmp/legacy/report_runner.py --report-type weekly",
                    "cwd": "/tmp/legacy",
                    "enabled": False,
                    "timeout_s": 60,
                    "notify": {"type": "feishu_dm", "user_open_id": "ou_old"},
                },
                {
                    "name": "solo-monthly-report",
                    "schedule": "15 * * * *",
                    "timezone": "UTC",
                    "command": "/tmp/legacy/report_runner.py --report-type monthly",
                    "cwd": "/tmp/legacy",
                    "enabled": False,
                    "timeout_s": 60,
                },
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    report_cron.ensure_report_jobs("solo", workspace=workspace)

    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    weekly = next(job for job in jobs if job["name"] == "solo-weekly-report")
    monthly = next(job for job in jobs if job["name"] == "solo-monthly-report")

    assert weekly["schedule"] == "0 21 * * 0"
    assert monthly["schedule"] == "0 21 28-31 * *"
    assert weekly["timezone"] == "Asia/Shanghai"
    assert monthly["timezone"] == "Asia/Shanghai"
    assert weekly["enabled"] is True
    assert monthly["enabled"] is True
    assert weekly["timeout_s"] == 900
    assert monthly["timeout_s"] == 900
    assert weekly["cwd"] == str(report_cron._REPO_ROOT)
    assert monthly["cwd"] == str(report_cron._REPO_ROOT)
    assert "notify" not in weekly


def test_solo_report_runner_generates_calendar_week_from_due_job(tmp_path: Path, monkeypatch) -> None:
    from solo.gateway.report_cron import ensure_report_jobs
    from solo.gateway import report_runner

    workspace = ensure_workspace(tmp_path / ".solo")
    ensure_report_jobs("solo", workspace=workspace)
    _update_job_next_run(
        workspace,
        "solo-weekly-report",
        "2026-06-07T13:00:00+00:00",
    )

    monkeypatch.setattr("solo.agent.OpenHarnessSoloAgent", lambda *args, **kwargs: object())
    pushed: list[str] = []

    async def _fake_push(workspace_arg: str | None, content: str) -> None:
        assert workspace_arg == str(workspace)
        pushed.append(content)

    monkeypatch.setattr(report_runner, "_push_report_to_im", _fake_push)
    assert asyncio.run(
        report_runner._main("solo", "weekly", str(workspace), "solo-weekly-report", "Asia/Shanghai")
    ) == 0

    reports = SoloStore(workspace).list_reports()
    assert len(reports) == 1
    assert reports[0].report_type == "weekly"
    assert reports[0].period_start == "2026-06-01"
    assert reports[0].period_end == "2026-06-07"
    assert pushed == [reports[0].content]


def test_solo_report_runner_monthly_guard_and_timezone_conversion(tmp_path: Path, monkeypatch) -> None:
    from solo.gateway.report_cron import ensure_report_jobs
    from solo.gateway import report_runner

    workspace = ensure_workspace(tmp_path / ".solo")
    ensure_report_jobs("solo", workspace=workspace)

    monkeypatch.setattr("solo.agent.OpenHarnessSoloAgent", lambda *args, **kwargs: object())
    pushed: list[str] = []

    async def _fake_push(workspace_arg: str | None, content: str) -> None:
        del workspace_arg
        pushed.append(content)

    monkeypatch.setattr(report_runner, "_push_report_to_im", _fake_push)

    _update_job_next_run(
        workspace,
        "solo-monthly-report",
        "2026-07-30T13:00:00+00:00",
    )
    assert asyncio.run(
        report_runner._main("solo", "monthly", str(workspace), "solo-monthly-report", "Asia/Shanghai")
    ) == 0
    assert SoloStore(workspace).list_reports() == []
    assert pushed == []

    _update_job_next_run(
        workspace,
        "solo-monthly-report",
        "2026-08-01T01:00:00+00:00",
        timezone_name="America/New_York",
    )
    assert asyncio.run(
        report_runner._main("solo", "monthly", str(workspace), "solo-monthly-report", "America/New_York")
    ) == 0

    reports = SoloStore(workspace).list_reports()
    assert len(reports) == 1
    assert reports[0].report_type == "monthly"
    assert reports[0].period_start == "2026-07-01"
    assert reports[0].period_end == "2026-07-31"
    assert pushed == [reports[0].content]
