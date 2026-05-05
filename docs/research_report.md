# OpenHarness Project Research Report

> Generated: 2026-05-04
> Based on 6 parallel sub-agent investigations of the full codebase.

---

## 1. Project Overview

**OpenHarness** is an open-source AI Agent Harness — a lightweight, extensible, and inspectable AI-powered CLI coding assistant. It is an open-source Python port of Claude Code that provides the "hands, eyes, memory, and safety boundaries" around LLMs.

| Attribute | Value |
|-----------|-------|
| **Version** | 0.1.7 |
| **License** | MIT |
| **Python** | ≥3.10 (target: py311) |
| **PyPI package** | `openharness-ai` |
| **Organization** | HKUDS (GitHub: github.com/HKUDS/OpenHarness) |
| **CLI entry points** | `oh` / `openh` / `openharness` / `ohmo` |

### Key Dependencies (Classified)

| Category | Packages |
|----------|----------|
| **LLM APIs** | `anthropic`, `openai`, `tiktoken`, `httpx` |
| **UI/Terminal** | `rich`, `prompt-toolkit`, `textual`, `pyperclip`, `questionary` |
| **Framework** | `typer`, `pydantic`, `loguru`, `jupyter` |
| **Integration** | `mcp`, `watchfiles`, `croniter`, `pyyaml`, `keyring` |
| **IM Channels** | `slack-sdk`, `python-telegram-bot`, `discord.py`, `lark-oapi` |
| **Dev** | `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy` |

---

## 2. Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI Layer (cli.py)                    │
│  oh / openharness / openh  (typer app)    ohmo (personal AI)│
├─────────────────────────────────────────────────────────────┤
│                     Commands (commands/)                     │
│  setup | auth | provider | mcp | plugin | cron              │
├───────────────────────┬─────────────────────────────────────┤
│                      Engine (engine/)                        │
│  QueryEngine ──► ToolPipeline ──► ToolRepair                 │
│  StreamEvents │ CostTracker │ ToolLoopGuard │ Messages       │
├──────────┬──────────┬──────────┬──────────┬─────────────────┤
│  API      │  Tools   │  Auth    │  UI      │  Bridge         │
│ (api/)    │ (tools/) │ (auth/) │ (ui/)    │ (bridge/)       │
│  Provider  │  40+     │  OAuth   │  TUI     │  Session Runner │
│  Registry  │  tools   │  Keyring │  React   │  Work Secret    │
├──────────┴──────────┴──────────┴──────────┴─────────────────┤
│                    Extension System                           │
│  Skills (skills/) │ Hooks (hooks/) │ Plugins (plugins/)      │
│  Themes (themes/) │ Keybindings   │ Output Styles            │
├─────────────────────────────────────────────────────────────┤
│                  Multi-Agent & Task System                    │
│  Tasks (tasks/) │ Swarm (swarm/) │ Coordinator (coordinator/)│
│  BackgroundTask  │ Subprocess/InProcess/tmux │ Coordinator    │
│  Manager         │ Mailbox │ Worktree │ TeamLifeycle        │
├─────────────────────────────────────────────────────────────┤
│                    Infrastructure                             │
│  Config (config/) │ Memory (memory/) │ State (state/)        │
│  Services (services/) │ Sandbox (sandbox/) │ MCP (mcp/)      │
│  Personalization (personalization/) │ Voice (voice/)         │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Core Engine (`src/openharness/engine/`)

### QueryEngine — The Central AI Loop

`QueryEngine` owns the entire session and drives the AI interaction cycle:

1. **`submit_message()`** → sends user input to LLM
2. **`_stream_query_with_guards()`** → wraps the query with:
   - **Auto-continue protection**: detects silent stops after tool execution, injects continuation prompt (max 1 consecutive, 5 absolute)
   - **MaxTurnsExceeded** guard
   - **Export checkpoint** for restore
3. **`run_query()`** (in `query.py`) — the actual AI interaction loop that:
   - Builds API requests with `tool_registry.to_api_schema()`
   - Streams LLM responses
   - Detects `ToolUseBlock` → dispatches to `_execute_tool_call()`
   - Appends tool results back to conversation messages

### ToolPipeline — 8-Stage Tool Execution

Each tool call passes through 8 independent stages:

```
resolve_tool → pre_hook → validate_input → check_permission
→ execute_tool → normalize_result → update_metadata → post_hook
```

Each stage returns `ToolPipelineState` with a `stop` flag that can short-circuit execution.

### Key Engine Components

| Component | File | Purpose |
|-----------|------|---------|
| `ToolRepair` | `tool_repair.py` | Fuzzy tool name matching with aliases |
| `ToolLoopGuard` | `tool_loop_guard.py` | Doom-loop detection (repeated failing calls) |
| `TextToolResultNormalizer` | `tool_result_normalizer.py` | Large result → file artifact, inline preview |
| `CostTracker` | `cost_tracker.py` | Token and cost accounting per session |
| `ConversationMessage` | `messages.py` | Message model with content blocks |
| `ToolMetadataKey` | `types.py` | 20+ metadata keys carried across turns |
| `StreamEvents` | `stream_events.py` | Event types emitted during streaming |

---

## 4. Provider & API Layer (`src/openharness/api/`)

### Architecture

```
API Client (abstract) ← AnthropicClient / OpenAIClient / CopilotClient / CodexClient
     ↑
ProviderRegistry — detects provider by model name prefix
     ↑
Provider Profiles — 10 built-in profiles
```

### 10 Built-in Provider Profiles

| Profile | Default Model | Format |
|---------|--------------|--------|
| `claude-api` | `claude-sonnet-4-6` | Anthropic |
| `claude-subscription` | `claude-sonnet-4-6` | Anthropic |
| `openai-compatible` | `gpt-5.4` | OpenAI |
| `codex` | `gpt-5.4` | OpenAI |
| `copilot` | `gpt-5.4` | Copilot |
| `moonshot` | `kimi-k2.5` | OpenAI |
| `gemini` | `gemini-2.5-flash` | OpenAI |
| `minimax` | `MiniMax-M2.7` | OpenAI |
| `deepseek` | `deepseek-v4-flash` | OpenAI |
| `qwen` | `qwen-plus` | OpenAI |

### Authentication Resolution

```
1. External auth (codex/claude subscription) → auth/external.py + auto-refresh
2. Copilot OAuth → copilot-managed
3. Profile-scoped API key (credential_slot) → keyring or file
4. Environment variables → per auth_source mapping
5. File-persisted credentials → auth/storage.py
```

---

## 5. CLI & Commands

### CLI Entry Points

All three aliases (`oh`, `openh`, `openharness`) point to the same Typer app. The `main()` callback starts an interactive session when no subcommand is given.

### Subcommand Tree

```
oh
├── setup            — one-shot configuration wizard
├── mcp {list,add,remove}
├── plugin {list,install,uninstall}
├── auth {login,status,logout,switch,copilot-login,copilot-logout,codex-login,claude-login}
├── provider {list,use,add,edit,remove}
└── cron {start,stop,status,list,toggle,history,logs}
```

### Flags (selected)

- `--model, -m` — model alias or full ID
- `--print, -p` — non-interactive mode
- `--continue, -c` / `--resume, -r` — session restore
- `--dry-run` — preview configuration without execution
- `--permission-mode` — default/plan/full_auto
- `--bare` — minimal mode (no hooks/plugins/MCP)
- `--effort` — low/medium/high/max
- `--system-prompt, -s` — override system prompt

---

## 6. Tool System (`src/openharness/tools/`)

### Architecture

```
BaseTool (ABC)
  ├── name, description, input_model (Pydantic)
  ├── execute(parsed_input, ToolExecutionContext) → ToolResult
  └── to_api_schema() → Anthropic API schema

ToolRegistry
  ├── register(tool), get(name), list_tools()
  ├── to_api_schema() → cached schema list
  └── unregister(name)

ToolExecutionContext
  └── cwd, metadata (tool_registry, ask_user, hook_executor, ...)
```

### 40+ Tools by Category

| Category | Tools |
|----------|-------|
| **File ops** | `read_file`, `write_file`, `edit_file`, `glob`, `grep` |
| **Shell** | `bash` |
| **Tasks** | `task_create/get/list/output/stop/update/wait` |
| **Agent** | `agent`, `send_message` |
| **Team** | `team_create`, `team_delete` |
| **Web** | `web_fetch`, `web_search` |
| **Code Intel** | `lsp` |
| **Notebook** | `notebook_edit` |
| **MCP** | `mcp__{server}__{tool}`, `list_mcp_resources`, `read_mcp_resource`, `mcp_auth` |
| **Other** | `ask_user_question`, `memory`, `sleep`, `todo`, `config`, `plan_mode`, `enter_worktree`, `exit_worktree`, `cron_manager`, `remote_trigger`, `image_to_text`, `tool_search`, `brief`, `skill_manager` |

---

## 7. Multi-Agent & Task Scheduling

### Task Lifecycle

```
PENDING → RUNNING → COMPLETED
                  → FAILED
                  → KILLED (via stop)
```

### BackgroundTaskManager (`tasks/manager.py`)

- Singleton manager managing tasks, processes, waiters, locks
- `create_shell_task()` — subprocess with stdout/stderr → log file
- `create_agent_task()` — resolves API key, spawns `python -m openharness --task-worker`
- `stop_task()` — SIGTERM → 0.5s → SIGKILL
- Output read via file offset cursors (incremental)

### Swarm Backend Selection (`swarm/registry.py`)

```
BackendRegistry.detect_backend():
  1. in_process  ← if previous in-process fallback activated
  2. tmux        ← if inside tmux session
  3. subprocess  ← always available (default)
```

| Backend | Isolation | Communication | Dependencies |
|---------|-----------|---------------|-------------|
| **Subprocess** | Independent process | stdin/stdout + file mailbox | None |
| **InProcess** | asyncio Task + ContextVar | in-memory Queue + file mailbox | None |
| **Tmux** | Terminal pane | tmux send-keys | tmux |

### Sub-Agent Types

| Type | Access | Use Case |
|------|--------|----------|
| `research` | Read-only | Codebase investigation |
| `worker` | Full read/write | Implementation, fixes |
| `verification` | Read-only + test runner | Validation, regression |
| `general-purpose` | Full access | General tasks |

### Mailbox System (`swarm/mailbox.py`)

```
~/.openharness/teams/<team>/agents/<agent_id>/inbox/<ts>_<id>.json
```

Message types: `user_message`, `permission_request/response`, `shutdown`, `idle_notification`
Atomic writes via tmp + os.replace.

### Coordinator Mode (`coordinator/coordinator_mode.py`)

Activated via `CLAUDE_CODE_COORDINATOR_MODE=1`. A leader agent decomposes tasks into Research → Synthesis → Implementation → Verification stages, spawning workers via `agent` tool and receiving results via XML `<task-notification>` messages.

### Worktree Isolation (`swarm/worktree.py`)

Git worktrees created at `~/.openharness/worktrees/<slug>/` with automatic symlinks for `node_modules`, `.venv`, `__pycache__`, `.tox` to save disk space.

---

## 8. Configuration System

### Layered Resolution

```
1. CLI args (--model, --base-url)
2. Environment vars (ANTHROPIC_API_KEY, OPENHARNESS_MODEL, etc.)
3. Config file ~/.openharness/settings.json
4. Built-in defaults
```

### Key Settings Structure

```python
class Settings(BaseModel):
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    provider: str = ""
    active_profile: str = "deepseek"
    profiles: dict[str, ProviderProfile]
    permission: PermissionSettings
    hooks: dict[str, list[HookDefinition]]
    memory: MemorySettings
    self_evolution: SelfEvolutionSettings
    sandbox: SandboxSettings
    mcp_servers: dict[str, McpServerConfig]
    theme: str = "default"
    effort: str = "medium"
    # ... 50+ fields
```

### Path Layout

| Path | Default |
|------|---------|
| Config dir | `~/.openharness/` |
| Data dir | `~/.openharness/data/` |
| Sessions | `~/.openharness/data/sessions/` |
| Tasks | `~/.openharness/data/tasks/` |
| Memory | `~/.openharness/data/memory/<project-hash>/` |
| Cron jobs | `~/.openharness/data/cron_jobs.json` |
| Plugins | `~/.openharness/plugins/` |
| Skills (user) | `~/.openharness/skills/` |
| Themes | `~/.openharness/themes/` |

---

## 9. Extension System

### Skills (`skills/`)

- **SkillDefinition**: name, description, content (Markdown), source (bundled/user/plugin)
- **SkillRegistry**: in-memory dict, no trigger-word matching built-in
- **Load order**: bundled (7 skills) → user → extra dirs → plugins
- **7 bundled skills**: `commit`, `debug`, `diagnose`, `plan`, `review`, `simplify`, `test`
- User skills stored at `~/.openharness/skills/<name>/SKILL.md`

### Hooks (`hooks/`)

10 lifecycle events:
`session_start/end`, `pre/post_compact`, `pre/post_tool_use`, `user_prompt_submit`, `notification`, `stop`, `subagent_stop`

4 hook types: `command` (shell), `prompt` (LLM validation), `http` (POST payload), `agent` (LLM + isolation)

### Plugins (`plugins/`)

- **PluginManifest** (Pydantic): name, version, enables skills/tools/hooks/MCP/commands/agents
- Loaded from `~/.openharness/plugins/<name>/` or project `.openharness/plugins/`
- Each plugin can contribute: skills dir, tools dir, hooks file, MCP config

### Themes & Keybindings

- 5 built-in themes: `default`, `dark`, `minimal`, `cyberpunk`, `solarized`
- Custom themes via `~/.openharness/themes/*.json`
- Default keybindings: `ctrl+l` → clear, `ctrl+k` → toggle vim, `ctrl+v` → toggle voice, `ctrl+t` → tasks
- Overridable via `~/.openharness/keybindings.json`

---

## 10. Memory & Personalization

### Memory System (`memory/`)

- File-based storage at `~/.openharness/data/memory/<project-hash>/`
- Operations: `add_memory_entry`, `remove_memory_entry`, `list_memory_files`
- Injection attack detection: 30+ regex patterns
- Providers: built-in + extensible interface

### Personalization (`personalization/`)

Session-end extraction of environmental facts:
- Detects: SSH hosts, conda envs, API endpoints, env vars, git remotes, Ray clusters, data paths
- Outputs: `rules.md` + `facts.json` at `~/.openharness/local_rules/`
- Confidence threshold: 0.9 for all patterns

---

## 11. UI Layer (`src/openharness/ui/`)

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **TUI App** | `textual` (Python) | Fallback terminal UI |
| **React Frontend** | `ink` + `react` (TypeScript) | Rich terminal UI with markdown rendering |
| **Permission Dialog** | Custom | Interactive approval for tool calls |
| **Protocol** | Abstract | UI backend API protocol |
| **BackendHost** | HTTP | Serves React TUI |
| **Runtime** | Python | Orchestration logic |

React TUI features:
- Markdown rendering (headings, lists, code fences, tables, blockquotes, links)
- Parallel tool-call tree rendering
- Command picker (`/`), skill picker (`/skills`), model switcher (`/model`)
- Permission mode switching (`/permissions`), provider selection (`/provider`)
- Session restore (`/resume`)
- Full-screen editor for long messages
- Tab completion, Shift+Enter for newlines, bracket paste mode

---

## 12. IM Channel Integration

10 supported channels via `channels/adapter.py`:

| Channel | Config Class |
|---------|-------------|
| Telegram | `TelegramConfig` |
| Slack | `SlackConfig` |
| Discord | `DiscordConfig` |
| Feishu (飞书) | `FeishuConfig` |
| DingTalk (钉钉) | `DingTalkConfig` |
| Email | `EmailConfig` |
| Matrix | `MatrixConfig` |
| QQ | `QQConfig` |
| WhatsApp | `WhatsAppConfig` |
| Mochat | `MochatConfig` |

All channels require explicit `allow_from` whitelist (default: empty, i.e. blocked).

---

## 13. Services

| Service | Purpose |
|---------|---------|
| **Cron Scheduler** | 30s tick, PID file, 300s job timeout, JSONL history |
| **Session Storage** | Save/restore sessions to `~/.openharness/data/sessions/` |
| **Token Estimation** | Model-specific token counting |
| **Tool Outputs** | Tool output artifact caching |
| **Sandbox (Docker)** | Container isolation with resource limits (optional) |
| **MCP** | Model Context Protocol client (STDIO + HTTP) |

---

## 14. Testing & CI

### Test Stats (current)

| Metric | Value |
|--------|-------|
| Total tests | 1,157 |
| Test modules | 30 subdirs + 6 top-level files |
| Coverage | 62% (20,925 stmts, 7,965 missed) |
| Source lines | ~30,901 (incl. ohmo/) |

### CI Pipeline (GitHub Actions)

3 parallel jobs on `ubuntu-latest`:

1. **python-tests** (matrix: 3.10, 3.11) — `pytest -q`
2. **python-quality** (3.11) — `ruff check src tests scripts`
3. **frontend-typecheck** (Node 20) — `npx tsc --noEmit`

### CI Gaps

| Gap | Impact |
|-----|--------|
| No coverage threshold in CI | Coverage could regress unnoticed |
| No mypy in CI | `mypy strict=true` configured but unused |
| No macOS/Windows CI | Platform-specific bugs undetected |
| No E2E in CI | All E2E scripts require real API keys |
| No frontend test runner | 12 `.test.tsx` files exist but never run |
| No `ruff format` check | Formatting can drift |
| 27 failing tests | Stable regressions from master |

### E2E Scripts (`scripts/`)

| Script | Scenarios |
|--------|-----------|
| `e2e_smoke.py` | 18 real-model scenarios (file IO, search, tasks, skills, MCP, agent, cron, worktree) |
| `test_harness_features.py` | Retry, skills, parallel tools, path permissions |
| `test_cli_flags.py` | CLI flag combinations |
| `test_real_skills_plugins.py` | Real skill loading from anthropic/skills |
| `test_docker_sandbox_e2e.py` | Docker lifecycle |
| `react_tui_e2e.py` | React TUI via pexpect |

---

## 15. Self-Evolution (Planned)

The DEV_TODO outlines upcoming self-evolution features:

- **Memory sedimentation**: Background agent evaluates conversation every N turns, writes user info to MEMORY.md/USER.md
- **Skill sedimentation**: Background agent creates/updates skills when tool call count exceeds threshold
- **Combined sedimentation**: Single evaluation when both conditions met
- **Hooks**: `pre_api_request` / `post_api_request`
- **read_file caching**: Return "file unchanged" for unmodified regions
- **Tool execution modes**: Blocking vs non-blocking tool distinction

---

## 16. Key Design Patterns

1. **Pipeline pattern**: Tool execution decomposed into 8 independent stages → testable, replaceable
2. **Plugin architecture**: Skills/hooks/tools/MCP all loadable from external sources
3. **Provider abstraction**: Single engine with multiple API format adapters (Anthropic, OpenAI, Copilot)
4. **Isolation layers**: Process-level (subprocess) → asyncio Task-level (in-process) → Worktree-level (git)
5. **Event-driven hooks**: 10 lifecycle events with command/HTTP/LLM hook types
6. **Layered config**: CLI > env > file > defaults with 10+ provider profiles
7. **Memory with security**: Injection attack detection (30+ regex patterns) on memory writes

---

## 17. Summary

OpenHarness is a **feature-complete open-source AI coding assistant** organized around:

- A **core engine** driving streaming AI interaction with tool-call cycles
- **40+ tools** for file operations, shell execution, web access, task management, and MCP
- **Multi-agent orchestration** via subprocess/in-process backends with worktree isolation
- **Extensible systems** through skills (Markdown files), hooks (lifecycle events), and plugins
- **10 provider profiles** supporting Anthropic, OpenAI, Copilot, Codex, and Chinese AI providers
- **Rich TUI** with React frontend and fallback Textual UI
- **IM channel integration** for 10 platforms
- **1,157 tests** at 62% coverage with active CI

The project is actively developed (v0.1.7, with DEV_TODO showing planned self-evolution and memory enhancement features) and appears to be a serious open-source alternative to Claude Code with significant architectural investment.
