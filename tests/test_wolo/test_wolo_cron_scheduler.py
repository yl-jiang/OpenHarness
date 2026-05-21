from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import wolo.gateway.cron_scheduler as scheduler
from wolo.workspace import ensure_workspace, get_data_dir, get_logs_dir


@pytest.fixture(autouse=True)
def _workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    workspace = ensure_workspace(tmp_path / ".wolo")
    monkeypatch.setattr(scheduler, "_WORKSPACE", workspace)
    return workspace


@pytest.mark.asyncio
async def test_wolo_scheduler_runs_one_shot_reminder_and_removes_job(
    monkeypatch: pytest.MonkeyPatch,
    _workspace: Path,
) -> None:
    from wolo.gateway.todo_cron import schedule_one_shot_reminder

    send_dm = AsyncMock()
    reformat = AsyncMock(return_value="should not be used")
    monkeypatch.setattr(scheduler, "_send_feishu_dm", send_dm)
    monkeypatch.setattr(scheduler, "_agent_reformat", reformat)

    job = schedule_one_shot_reminder(
        "wolo",
        workspace=_workspace,
        remind_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        message="喝水",
        notify={"type": "feishu_dm", "user_open_id": "ou_user", "workspace": str(_workspace)},
    )

    await scheduler.run_scheduler_loop(once=True)

    history_path = get_data_dir(_workspace) / "cron_history.jsonl"
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    jobs_path = get_data_dir(_workspace) / "cron_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

    assert history[-1]["name"] == job["name"]
    assert history[-1]["status"] == "success"
    assert "喝水" in history[-1]["stdout"]
    assert all(item["name"] != job["name"] for item in jobs)
    reformat.assert_not_awaited()
    send_dm.assert_awaited_once()
    assert send_dm.await_args.kwargs["user_open_id"] == "ou_user"
    assert "喝水" in send_dm.await_args.kwargs["content"]
    assert get_logs_dir(_workspace).exists()
