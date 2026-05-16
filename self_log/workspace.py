"""Workspace helpers for the standalone self-log app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from self_log.models import SelfLogConfig

WORKSPACE_DIRNAME = ".self-log"
CONFIG_FILENAME = "config.json"


def get_workspace_root(workspace: str | Path | None = None) -> Path:
    explicit = workspace or os.environ.get("SELF_LOG_WORKSPACE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return (Path.home() / WORKSPACE_DIRNAME).resolve()


def get_config_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / CONFIG_FILENAME


def get_data_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "data"


def get_logs_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "logs"


def get_state_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "state.json"


def get_pid_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "gateway.pid"


def ensure_workspace(workspace: str | Path | None = None) -> Path:
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    get_data_dir(root).mkdir(parents=True, exist_ok=True)
    get_logs_dir(root).mkdir(parents=True, exist_ok=True)
    return root


def initialize_workspace(workspace: str | Path | None = None) -> Path:
    root = ensure_workspace(workspace)
    config_path = get_config_path(root)
    if not config_path.exists():
        config_path.write_text(SelfLogConfig().model_dump_json(indent=2) + "\n", encoding="utf-8")
    state_path = get_state_path(root)
    if not state_path.exists():
        state_path.write_text(
            json.dumps({"app": "self-log", "workspace": str(root)}, indent=2) + "\n",
            encoding="utf-8",
        )
    return root


def workspace_health(workspace: str | Path | None = None) -> dict[str, bool]:
    root = get_workspace_root(workspace)
    return {
        "workspace": root.exists(),
        "data_dir": get_data_dir(root).exists(),
        "logs_dir": get_logs_dir(root).exists(),
        "config": get_config_path(root).exists(),
        "state": get_state_path(root).exists(),
    }
