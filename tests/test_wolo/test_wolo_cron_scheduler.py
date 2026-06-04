from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import wolo.gateway.cron_scheduler as scheduler
from wolo.core.workspace import ensure_workspace, get_data_dir, get_logs_dir


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


@pytest.mark.asyncio
async def test_wolo_scheduler_runs_agent_task_and_removes_job(
    monkeypatch: pytest.MonkeyPatch,
    _workspace: Path,
) -> None:
    from wolo.gateway.todo_cron import schedule_one_shot_agent_task

    send_dm = AsyncMock()
    reformat = AsyncMock(return_value="should not be used")
    monkeypatch.setattr(scheduler, "_send_feishu_dm", send_dm)
    monkeypatch.setattr(scheduler, "_agent_reformat", reformat)

    job = schedule_one_shot_agent_task(
        "wolo",
        workspace=_workspace,
        run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        prompt="生成本周周报",
        notify={"type": "feishu_dm", "user_open_id": "ou_user", "workspace": str(_workspace)},
    )

    mock_runner = AsyncMock()
    mock_runner.run = AsyncMock(return_value="本周你做了很多事情！")

    with patch("wolo.gateway.cron_scheduler._run_agent_task", new=AsyncMock(return_value="本周你做了很多事情！")):
        await scheduler.run_scheduler_loop(once=True)

    history_path = get_data_dir(_workspace) / "cron_history.jsonl"
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines()]
    jobs_path = get_data_dir(_workspace) / "cron_jobs.json"
    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))

    assert history[-1]["name"] == job["name"]
    assert history[-1]["status"] == "success"
    assert "本周你做了很多事情" in history[-1]["stdout"]
    assert all(item["name"] != job["name"] for item in jobs)
    reformat.assert_not_awaited()
    send_dm.assert_awaited_once()
    assert "本周你做了很多事情" in send_dm.await_args.kwargs["content"]


def test_wolo_ensure_todo_reminder_job_reconciles_existing_drift(_workspace: Path) -> None:
    from wolo.gateway import todo_cron

    jobs_path = get_data_dir(_workspace) / "cron_jobs.json"
    jobs_path.write_text(
        json.dumps(
            [
                {
                    "name": "wolo-todo-reminder",
                    "schedule": "5 * * * *",
                    "timezone": "UTC",
                    "command": "/tmp/legacy/scripts/todo_reminder.py --app wolo",
                    "cwd": "/tmp/legacy",
                    "enabled": False,
                    "notify": {"type": "feishu_dm", "user_open_id": "ou_old"},
                }
            ],
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    notify = {"type": "feishu_dm", "user_open_id": "ou_user", "workspace": str(_workspace)}
    todo_cron.ensure_todo_reminder_job("wolo", workspace=_workspace, notify=notify)

    jobs = json.loads(jobs_path.read_text(encoding="utf-8"))
    job = next(item for item in jobs if item["name"] == "wolo-todo-reminder")
    assert job["schedule"] == "0 9 * * *"
    assert job["timezone"] == "Asia/Shanghai"
    assert job["enabled"] is True
    assert job["cwd"] == str(todo_cron._REPO_ROOT)
    assert f"{sys.executable} {todo_cron._APP_ROOT / 'gateway' / 'todo_reminder.py'}" in job["command"]
    assert str(_workspace) in job["command"]
    assert job["notify"] == notify


@pytest.mark.asyncio
async def test_wolo_execute_job_uses_custom_timeout(
    monkeypatch: pytest.MonkeyPatch,
    _workspace: Path,
) -> None:
    timeout_seen: dict[str, int] = {}
    notify = AsyncMock()

    class _Process:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return (b"weekly report archived", b"")

    async def _fake_wait_for(awaitable, timeout):
        timeout_seen["value"] = timeout
        return await awaitable

    monkeypatch.setattr(scheduler, "_notify_job_result", notify)
    monkeypatch.setattr(scheduler, "create_shell_subprocess", AsyncMock(return_value=_Process()))
    monkeypatch.setattr(scheduler.asyncio, "wait_for", _fake_wait_for)

    entry = await scheduler.execute_job(
        {
            "name": "wolo-weekly-report",
            "command": "echo report",
            "cwd": str(_workspace),
            "timeout_s": 901,
        }
    )

    assert timeout_seen["value"] == 901
    assert entry["status"] == "success"
    assert "weekly report archived" in entry["stdout"]
    notify.assert_awaited_once()
