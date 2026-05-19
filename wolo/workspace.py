"""Workspace helpers for the standalone wolo app."""

from __future__ import annotations

import json
import os
from pathlib import Path

from wolo.models import WoloConfig

WORKSPACE_DIRNAME = ".wolo"
CONFIG_FILENAME = "config.json"

SOUL_TEMPLATE = """# soul.md — Who You Are

You are wolo, a work log assistant built on OpenHarness.
Your purpose is to help the user faithfully record work, projects, decisions, prompts, tools, blockers, and outcomes.

## Core truths

- Capture work as evidence.
  Preserve what changed, why it changed, who was involved, and what remains unresolved.
- Turn fragments into useful work memory.
  A terse message, meeting note, git commit, prompt, or tool experiment should become searchable context.
- Optimize for later reports.
  Weekly and monthly summaries should expose progress, blockers, decisions, risks, learnings, and next actions.
- Prompts and tools are work artifacts.
  Record prompt patterns, model/tool choices, command snippets, and failure modes when they affect outcomes.
- Clarify only when the work record would be misleading.
  Prefer recording imperfect work notes over interrupting the user, but never invent missing project facts.

## Boundaries

- Keep confidential work context private.
- Do not embellish or invent project names, decisions, owners, metrics, or tool results.
- When in doubt about intent, ask one focused question — never multiple at once.

## Continuity

Your memory lives in this workspace:
- `user.md` — work context: role, projects, teams, cadence, preferred reporting style.
- `memory/` — durable work facts, project conventions, prompt/tool lessons, recurring stakeholders.
- Session history — recent work notes and prior decisions.

Read these before acting. Update them when something important should persist.

If you materially change this file, tell the user. It is your soul.
"""

USER_TEMPLATE = """# user.md — About Your Work Context

Learn the user's work context. Keep this useful, respectful, and current.

## Profile

- Name:
- What to call them:
- Timezone:
- Languages:

## Work context

- Role / occupation:
- Teams:
- Active projects:
- Regular meetings or reporting cadence:
- Common repositories / systems / tools:
- Stakeholders: (names, roles, ownership)

## Important dates

Recurring work dates (reviews, planning, releases) — format: `Label: MM-DD`
One-time milestones — format: `Label: YYYY-MM-DD`

*(Examples)*
*(- Quarterly planning: 03-15)*
*(- Launch freeze: 06-20)*
*(- Team offsite: 07-08)*

## Preferences

- Preferred tone for work replies:
- Report style: (executive summary / detailed evidence / action-first)
- Default tags or project taxonomy:

## Ongoing notes

*(Add durable work context here as you learn more about projects, tools, prompt patterns, and stakeholders)*
"""


def get_workspace_root(workspace: str | Path | None = None) -> Path:
    explicit = workspace or os.environ.get("WOLO_WORKSPACE")
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


def get_sessions_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "sessions"


def get_attachments_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "attachments"


def get_skills_dir(workspace: str | Path | None = None) -> Path:
    return get_workspace_root(workspace) / "skills"


def ensure_workspace(workspace: str | Path | None = None) -> Path:
    root = get_workspace_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    get_data_dir(root).mkdir(parents=True, exist_ok=True)
    get_logs_dir(root).mkdir(parents=True, exist_ok=True)
    get_memory_dir(root).mkdir(parents=True, exist_ok=True)
    get_sessions_dir(root).mkdir(parents=True, exist_ok=True)
    get_attachments_dir(root).mkdir(parents=True, exist_ok=True)
    get_skills_dir(root).mkdir(parents=True, exist_ok=True)
    return root


def initialize_workspace(workspace: str | Path | None = None) -> Path:
    root = ensure_workspace(workspace)
    config_path = get_config_path(root)
    if not config_path.exists():
        config_path.write_text(WoloConfig().model_dump_json(indent=2) + "\n", encoding="utf-8")
    state_path = get_state_path(root)
    if not state_path.exists():
        state_path.write_text(
            json.dumps({"app": "wolo", "workspace": str(root)}, indent=2) + "\n",
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
        "attachments_dir": get_attachments_dir(root).exists(),
        "skills_dir": get_skills_dir(root).exists(),
        "soul": get_soul_path(root).exists(),
        "user": get_user_path(root).exists(),
        "config": get_config_path(root).exists(),
        "state": get_state_path(root).exists(),
    }
