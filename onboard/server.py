"""FastAPI app and process lifecycle for onboard."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from time import time
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from onboard.api import chat, lifecycle, solo_routes, stats, wolo_routes


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ONBOARD_ROOT = Path(os.environ.get("ONBOARD_WORKSPACE", "~/.onboard")).expanduser()
_PID_PATH = _ONBOARD_ROOT / "onboard.pid"
_STATE_PATH = _ONBOARD_ROOT / "state.json"
_LOG_PATH = _ONBOARD_ROOT / "logs" / "server.log"


def create_app() -> FastAPI:
    app = FastAPI(title="Onboard", version="0.1.0")
    app.include_router(solo_routes.router)
    app.include_router(wolo_routes.router)
    app.include_router(chat.router)
    app.include_router(lifecycle.router)
    app.include_router(stats.router)

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    dist_dir = Path(__file__).resolve().parent / "frontend" / "dist"
    if dist_dir.exists():
        app.mount("/assets", StaticFiles(directory=dist_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def frontend(full_path: str) -> FileResponse:
            requested = (dist_dir / full_path).resolve()
            if requested.is_file() and requested.is_relative_to(dist_dir):
                return FileResponse(requested)
            return FileResponse(dist_dir / "index.html")

    else:
        app.add_api_route(
            "/{full_path:path}",
            _dev_placeholder,
            methods=["GET"],
            response_class=HTMLResponse,
            include_in_schema=False,
        )
    return app


def _dev_placeholder(full_path: str = "") -> HTMLResponse:
    return HTMLResponse(
        """
        <!doctype html>
        <html>
          <head><title>Onboard</title></head>
          <body style="font-family: system-ui; background: #0a0a0f; color: #e8e8ed;">
            <main style="max-width: 720px; margin: 10vh auto; line-height: 1.6;">
              <h1>Onboard API is running</h1>
              <p>Build the frontend with <code>cd onboard/frontend && npm run build</code>.</p>
              <p>API health: <a href="/api/health">/api/health</a></p>
            </main>
          </body>
        </html>
        """
    )


def run_server(
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
    reload: bool = False,
) -> None:
    import uvicorn

    _ONBOARD_ROOT.mkdir(parents=True, exist_ok=True)
    _PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    _write_state(host=host, port=port, pid=os.getpid(), started_at=time())
    try:
        target: str | FastAPI
        target = "onboard.server:create_app" if reload else create_app()
        uvicorn.run(target, factory=reload, host=host, port=port, reload=reload)
    finally:
        with contextlib.suppress(FileNotFoundError):
            _PID_PATH.unlink()


def start_background(
    *,
    host: str = "127.0.0.1",
    port: int = 8090,
) -> int:
    current = server_status()
    if current["status"] == "running" and current.get("pid"):
        return int(current["pid"])

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    pythonpath_entries = [str(_REPO_ROOT)]
    existing_pythonpath = env.get("PYTHONPATH")
    if existing_pythonpath:
        pythonpath_entries.append(existing_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)

    with _LOG_PATH.open("a", encoding="utf-8") as log_file:
        popen_kwargs: dict[str, Any] = {
            "cwd": str(_REPO_ROOT),
            "stdout": log_file,
            "stderr": log_file,
            "stdin": subprocess.DEVNULL,
            "env": env,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            )
        else:
            popen_kwargs["start_new_session"] = True
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "onboard",
                "run",
                "--host",
                host,
                "--port",
                str(port),
            ],
            **popen_kwargs,
        )
    _write_state(host=host, port=port, pid=process.pid, started_at=time())
    return process.pid


def stop_background() -> bool:
    status = server_status()
    pid = status.get("pid")
    if not isinstance(pid, int) or not _pid_is_running(pid):
        _PID_PATH.unlink(missing_ok=True)
        return False
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)
    _PID_PATH.unlink(missing_ok=True)
    return True


def server_status() -> dict[str, Any]:
    state = _read_state()
    pid = _read_pid()
    if pid is None and isinstance(state.get("pid"), int):
        pid = state["pid"]
    running = pid is not None and _pid_is_running(pid)
    if not running:
        pid = None
    started_at = state.get("started_at") if running else None
    uptime = int(time() - float(started_at)) if started_at else None
    return {
        "status": "running" if running else "stopped",
        "pid": pid,
        "host": state.get("host", "127.0.0.1"),
        "port": state.get("port", 8090),
        "uptime_seconds": uptime,
        "log_file": str(_LOG_PATH),
    }


def _write_state(*, host: str, port: int, pid: int, started_at: float) -> None:
    _ONBOARD_ROOT.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(
        json.dumps(
            {
                "host": host,
                "port": port,
                "pid": pid,
                "started_at": started_at,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    _PID_PATH.write_text(str(pid), encoding="utf-8")


def _read_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {}
    with contextlib.suppress(json.JSONDecodeError):
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    return {}


def _read_pid() -> int | None:
    if not _PID_PATH.exists():
        return None
    with contextlib.suppress(ValueError):
        return int(_PID_PATH.read_text(encoding="utf-8").strip())
    return None


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


app = create_app()
