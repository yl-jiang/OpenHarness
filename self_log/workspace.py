"""Workspace helpers for the standalone self-log app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from self_log.models import SelfLogConfig

WORKSPACE_DIRNAME = ".self-log"
CONFIG_FILENAME = "config.json"

SOUL_TEMPLATE = """# soul.md — Who You Are

You are self-log, a personal growth journal assistant built on OpenHarness.
Your purpose is to help the user faithfully record, reflect on, and grow from their daily experiences.

## Core truths

- Every moment worth recording is worth preserving well.
  Help the user capture life clearly, even when their input is messy or fragmented.
- Notice patterns over time.
  A single day is a data point; weeks and months reveal stories of growth.
- Be honest in what you observe.
  When generating reports and reflections, don't just summarize — observe, highlight, and encourage.
- Access is intimacy.
  Diary entries, health records, and personal moments are precious. Treat them with care and respect.
- Clarify only when truly necessary.
  Record first, refine later. Never make the user feel interrogated about their own life.

## Boundaries

- Private things stay private.
- Do not embellish or invent details that the user did not provide.
- When in doubt about intent, ask one focused question — never multiple at once.

## Continuity

Your memory lives in this workspace:
- `user.md` — who the user is: their life context, relationships, and habits.
- `memory/` — durable facts and recurring context about the user.
- Session history — what was discussed and recorded recently.

Read these before acting. Update them when something important should persist.

If you materially change this file, tell the user. It is your soul.
"""

USER_TEMPLATE = """# user.md — About Your User

Learn the person whose life you are helping to record. Keep this useful, respectful, and current.

## Profile

- Name:
- What to call them:
- Timezone:
- Languages:

## Life context

- Work / Occupation:
- Important people: (family members, close friends — names and relationships)
- Regular habits or routines:
- Health notes (if user has shared):
- Common locations: (home city, office, etc.)

## Preferences

- Preferred tone for replies:
- Entry style: (detailed / brief / emoji-friendly)

## Ongoing notes

*(Add context here as you learn more about the user)*
"""


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


def get_soul_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "soul.md"


def get_user_path(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "user.md"


def get_memory_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "memory"


def get_memory_index_path(workspace: str | Path | None = None) -> Path:
    return get_memory_dir(workspace) / "MEMORY.md"


def ensure_workspace(workspace: str | Path | None = None) -> Path:
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    get_data_dir(root).mkdir(parents=True, exist_ok=True)
    get_logs_dir(root).mkdir(parents=True, exist_ok=True)
    get_memory_dir(root).mkdir(parents=True, exist_ok=True)
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
    soul_path = get_soul_path(root)
    if not soul_path.exists():
        soul_path.write_text(SOUL_TEMPLATE.strip() + "\n", encoding="utf-8")
    user_path = get_user_path(root)
    if not user_path.exists():
        user_path.write_text(USER_TEMPLATE.strip() + "\n", encoding="utf-8")
    return root


def workspace_health(workspace: str | Path | None = None) -> dict[str, bool]:
    root = get_workspace_root(workspace)
    return {
        "workspace": root.exists(),
        "data_dir": get_data_dir(root).exists(),
        "logs_dir": get_logs_dir(root).exists(),
        "memory_dir": get_memory_dir(root).exists(),
        "soul": get_soul_path(root).exists(),
        "user": get_user_path(root).exists(),
        "config": get_config_path(root).exists(),
        "state": get_state_path(root).exists(),
    }
