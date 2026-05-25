import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

import onboard.server as onboard_server


def _patch_onboard_workspace(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> None:
    monkeypatch.setattr(onboard_server, "_ONBOARD_ROOT", workspace)
    monkeypatch.setattr(onboard_server, "_PID_PATH", workspace / "onboard.pid")
    monkeypatch.setattr(onboard_server, "_STATE_PATH", workspace / "state.json")
    monkeypatch.setattr(onboard_server, "_LOG_PATH", workspace / "logs" / "server.log")


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
