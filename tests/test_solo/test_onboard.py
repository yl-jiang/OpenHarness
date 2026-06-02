import socket
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

import onboard.server as onboard_server
from onboard.services.solo_service import SoloService
from onboard.services.wolo_service import WoloService
from solo.core.models import SoloRecord, SoloReport


def _patch_onboard_workspace(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setattr(onboard_server, "_ONBOARD_ROOT", workspace)
    monkeypatch.setattr(onboard_server, "_PID_PATH", workspace / "onboard.pid")
    monkeypatch.setattr(onboard_server, "_STATE_PATH", workspace / "state.json")
    monkeypatch.setattr(onboard_server, "_LOG_PATH", workspace / "logs" / "server.log")


def test_solo_onboard_lists_latest_items_first(tmp_path: Path) -> None:
    service = SoloService(tmp_path)
    old_entry = service.store.record("old entry", created_at="2026-05-25T00:00:00+00:00")
    new_entry = service.store.record("new entry", created_at="2026-05-26T00:00:00+00:00")
    service.store.add_record(
        SoloRecord(
            id="old-record",
            entry_id=old_entry.id,
            date="2026-05-25",
            raw_content="old",
            corrected_content="old",
            summary="old",
            tags="",
            emotion="neutral",
            created_at="2026-05-25T00:00:01+00:00",
        )
    )
    service.store.add_record(
        SoloRecord(
            id="new-record",
            entry_id=new_entry.id,
            date="2026-05-26",
            raw_content="new",
            corrected_content="new",
            summary="new",
            tags="",
            emotion="neutral",
            created_at="2026-05-26T00:00:01+00:00",
        )
    )
    service.store.add_report(
        SoloReport(
            id="old-report",
            report_type="weekly",
            content="old",
            created_at="2026-05-25T00:00:02+00:00",
        )
    )
    service.store.add_report(
        SoloReport(
            id="new-report",
            report_type="weekly",
            content="new",
            created_at="2026-05-26T00:00:02+00:00",
        )
    )

    assert service.list_entries(limit=1, offset=0)["items"][0]["id"] == new_entry.id
    assert service.list_records(limit=1, offset=0)["items"][0]["id"] == "new-record"
    assert service.list_reports()[0]["id"] == "new-report"


@pytest.mark.parametrize(
    ("service_cls", "workspace_name"),
    [
        (SoloService, ".solo"),
        (WoloService, ".wolo"),
    ],
)
def test_onboard_stats_include_llm_usage_breakdown(service_cls, workspace_name: str, tmp_path: Path) -> None:
    service = service_cls(tmp_path / workspace_name)
    service.store.record_llm_call("gpt-5")
    service.store.record_llm_call("gpt-5")
    service.store.record_llm_call("claude-sonnet-4.5")

    stats = service.stats()

    assert stats["llm_total_calls"] == 3
    assert {item["model"]: item["count"] for item in stats["llm_usage_models"]} == {
        "gpt-5": 2,
        "claude-sonnet-4.5": 1,
    }


def test_run_server_does_not_overwrite_state_when_port_is_busy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / ".onboard"
    workspace.mkdir()
    _patch_onboard_workspace(monkeypatch, workspace)

    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    onboard_server._write_state(host="127.0.0.1", port=port, pid=1234, started_at=1.0)
    state_before = (workspace / "state.json").read_text(encoding="utf-8")
    pid_before = (workspace / "onboard.pid").read_text(encoding="utf-8")

    try:
        with pytest.raises(onboard_server.OnboardServerError, match="address already in use"):
            onboard_server.run_server(host="127.0.0.1", port=port)
    finally:
        listener.close()

    assert (workspace / "state.json").read_text(encoding="utf-8") == state_before
    assert (workspace / "onboard.pid").read_text(encoding="utf-8") == pid_before


def test_solo_onboard_run_reports_bind_errors_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    from solo.cli import app

    def fake_run_server(*, host: str = "127.0.0.1", port: int = 8090, reload: bool = False) -> None:
        raise onboard_server.OnboardServerError(
            "onboard cannot bind http://127.0.0.1:8090: address already in use"
        )

    monkeypatch.setattr(onboard_server, "run_server", fake_run_server)

    result = CliRunner().invoke(app, ["onboard", "run"])

    assert result.exit_code == 1
    assert "onboard cannot bind http://127.0.0.1:8090: address already in use" in result.output


def test_run_server_configures_openharness_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / ".onboard"
    workspace.mkdir()
    _patch_onboard_workspace(monkeypatch, workspace)

    captured: dict[str, object] = {}

    def fake_configure_logging(*, level: str, **_: object) -> None:
        captured["level"] = level

    @contextmanager
    def fake_reserve_listener(*, host: str, port: int):
        captured["host"] = host
        captured["port"] = port

        class _Listener:
            def fileno(self) -> int:
                return 123

        yield _Listener()

    def fake_write_state(*, host: str, port: int, pid: int, started_at: float) -> None:
        captured["state"] = {
            "host": host,
            "port": port,
            "pid": pid,
            "started_at": started_at,
        }

    def fake_run(target: object, *, factory: bool, fd: int, reload: bool) -> None:
        captured["uvicorn"] = {
            "target": target,
            "factory": factory,
            "fd": fd,
            "reload": reload,
        }

    monkeypatch.setattr(onboard_server, "configure_logging", fake_configure_logging)
    monkeypatch.setattr(onboard_server, "_build_frontend", lambda: None)
    monkeypatch.setattr(onboard_server, "_write_state", fake_write_state)
    monkeypatch.setattr(onboard_server, "_reserve_listener", fake_reserve_listener)
    monkeypatch.setattr(onboard_server, "get_token", lambda: "test-token")
    monkeypatch.setitem(sys.modules, "uvicorn", types.SimpleNamespace(run=fake_run))
    monkeypatch.setenv("OPENHARNESS_LOG_LEVEL", "DEBUG")

    onboard_server.run_server(host="127.0.0.1", port=8091)

    assert captured["level"] == "DEBUG"
    uvicorn_call = captured["uvicorn"]
    assert uvicorn_call["factory"] is False
    assert uvicorn_call["fd"] == 123
    assert uvicorn_call["reload"] is False
    assert uvicorn_call["target"].title == "Onboard"
