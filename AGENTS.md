# Agent Instructions for OpenHarness

## Setup
- `uv sync --extra dev` (uses Alibaba Cloud PyPI mirror by default, see `pyproject.toml`)
- Frontend: `cd frontend/terminal && npm ci`

## Verification
- Lint: `uv run ruff check src tests scripts solo wolo`
- Test: `uv run pytest -q`
- Frontend typecheck: `cd frontend/terminal && npx tsc --noEmit`
- Solo tests: `uv run pytest -q tests/test_solo`
- Wolo tests: `uv run pytest -q tests/test_wolo`

## Architecture
- Core harness: `src/openharness/` (agent loop, tools, skills, memory, permissions, hooks, MCP, commands, config, sandbox, coordinator, tasks, prompts, ui)
- Personal agent: `ohmo/` (runs on Claude Code/Codex subscriptions, `~/.ohmo` workspace)
- Personal journal: `solo/` (standalone app, `~/.solo` workspace)
- Work log: `wolo/` (standalone app, `~/.wolo` workspace)
- Frontend TUI: `frontend/terminal/` (React + Ink)
- Entry points: `oh`/`openh`/`openharness` → OpenHarness, `ohmo` → personal agent, `solo` → personal journal, `wolo` → work log
- Skills: `src/openharness/skills/` + `~/.openharness/skills/` + project-local `.openharness/skills/` (Markdown, loaded at runtime)
- Plugins: `src/openharness/plugins/` + `~/.openharness/plugins/`

## Key Facts
- Python >=3.10 (CI: 3.10, 3.11)
- `uv run oh --dry-run` previews resolved settings without execution
- CHANGELOG.md `[Unreleased]` tracks user-visible changes
- CI matches PR template: ruff + pytest + (frontend tsc if touched)
- solo/wolo are independent packages sharing OpenHarness infrastructure (model, auth, channels, tools)
- solo/wolo have their own workspaces, configs, gateways — do NOT couple them back into ohmo
