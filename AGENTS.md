# Agent Instructions for OpenHarness

## Setup
- `uv sync --extra dev` (uses Alibaba Cloud PyPI mirror by default, see `pyproject.toml`)
- Frontend: `cd frontend/terminal && npm ci`

## Verification
- Lint: `uv run ruff check src tests scripts`
- Test: `uv run pytest -q`
- Frontend typecheck: `cd frontend/terminal && npx tsc --noEmit`

## Architecture
- Core harness: `src/openharness/` (agent loop, tools, skills, memory)
- Personal agent: `ohmo/` (runs on Claude Code/Codex subscriptions)
- Frontend TUI: `frontend/terminal/` (React + Ink)
- Entry points: `oh`/`openh`/`openharness` → OpenHarness, `ohmo` → personal agent
- Skills: `src/openharness/skills/` + `~/.openharness/skills/` (Markdown, loaded at runtime)
- Plugins: `src/openharness/plugins/` + `~/.openharness/plugins/`

## Key Facts
- Python >=3.10 (CI: 3.10, 3.11)
- `uv run oh --dry-run` previews resolved settings without execution
- CHANGELOG.md `[Unreleased]` tracks user-visible changes
- CI matches PR template: ruff + pytest + (frontend tsc if touched)
