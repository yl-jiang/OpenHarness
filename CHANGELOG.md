# Changelog

All notable changes to OpenHarness should be recorded in this file.

The format is based on Keep a Changelog, and this project currently tracks changes in a lightweight, repository-oriented way.

## [Unreleased]

### Added

- `self-log` is now a standalone app/package with its own `~/.self-log` workspace, config, CLI, gateway bridge/service, OpenHarness-backed domain agent, model-structured bulk import, zero-guess pending confirmations, reports, reminders, and self-log-only tools.
- `settings.json` now supports `max_children` to configure the primary session's total managed subagent/background-agent child budget instead of always using the built-in default of 16, and accepts `"infinity"` for an unbounded budget.
- Shell injection support in the React TUI and skills:
  - Pressing **bare `!`** in the chat composer (or typing `!cmd`) opens a one-shot shell prompt. The command is dispatched to the built-in `bash` tool with `origin="user_shell"`, runs under the existing permission model, and its output is injected back into the conversation as a tool result the model can read.
  - Type `exit` / `quit` (or press `Esc` on an empty buffer) to leave shell mode.
  - Skill Markdown can opt in to template-time shell substitution by setting `shell-injection: true` (or `shell_injection: true`) in frontmatter. Inside the skill body, `!{cmd}` is replaced with the captured stdout/stderr of `cmd`. All commands are stage-authorized before any execution, and argument placeholders (`$1`, `$ARGUMENTS`, …) are shell-escaped with `shlex.quote` before substitution.
  - A new transcript role `user_shell` renders user-initiated shell commands with a `!` warning-coloured prefix.
  - React TUI shell mode now supports bash-style inline Tab completion for executable names from `PATH` and for filesystem paths in command arguments, cycling through matches in place without opening a picker.
- Built-in `qwen` provider profile so `oh setup` offers Qwen (DashScope) as a first-class provider choice, with `dashscope_api_key` auth source, `qwen-plus` as the default model, and the DashScope OpenAI-compatible endpoint.
- `oh --dry-run` safe preview mode for inspecting resolved runtime settings, auth state, prompt assembly, commands, skills, tools, and configured MCP servers without executing the model or tools.
- Docker as an alternative sandbox backend (`sandbox.backend = "docker"`) for stronger execution isolation with configurable resource limits, network isolation, and automatic image management.
- Built-in `gemini` provider profile so `oh setup` offers Google Gemini as a first-class provider choice, with `gemini_api_key` auth source and `gemini-2.5-flash` as the default model.
- `diagnose` skill: trace agent run failures and regressions using structured evidence from run artifacts.
- Skill frontmatter now supports `disable-model-invocation` for hiding a skill from model auto-discovery and `user-invocable` for hiding it from user-facing `/skills` entry points.
- OpenAI-compatible API client (`--api-format openai`) supporting any provider that implements the OpenAI `/v1/chat/completions` format, including Alibaba DashScope, DeepSeek, GitHub Models, Groq, Together AI, Ollama, and more.
- `OPENHARNESS_API_FORMAT` environment variable for selecting the API format.
- `OPENAI_API_KEY` fallback when using OpenAI-format providers.
- GitHub Actions CI workflow for Python linting, tests, and frontend TypeScript checks.
- `CONTRIBUTING.md` with local setup, validation commands, and PR expectations.
- `docs/SHOWCASE.md` with concrete OpenHarness usage patterns and demo commands.
- GitHub issue templates and a pull request template.
- React TUI assistant messages now render structured Markdown blocks, including headings, lists, code fences, blockquotes, links, and tables.
- Built-in `codex` output style for compact, low-noise transcript rendering in React TUI.
- React TUI `@` file mentions and `/skills` picker for manually loading a selected skill into the current session.
- React TUI prompt composer now has a clickable expand affordance that opens a fullscreen editor for long drafts, keeps Enter/Shift+Enter as newline-only inside that view, and preserves leading slash command/skill completion via tab.

### Changed

- `/export` now writes a timestamped Markdown session export into the current working directory by default, using a richer Kimi-style transcript format with frontmatter, overview, turns, tool calls, and tool results.
- **Approval architecture refactor**: Consolidated three scattered approval entry points into a single `ApprovalCoordinator` subsystem (`src/openharness/permissions/approvals.py`).  `PermissionChecker` is now a pure policy engine (no session memory); all remembered-approval state lives in `ApprovalState` inside `ApprovalCoordinator`.  Preview-capable tools (`edit_file`, `write_file`) defer the soft-confirmation check to the richer diff-preview prompt so users see exactly one approval modal per file write.  Approval state persists correctly across conversation turns via `QueryEngine._approval_coordinator`.

### Fixed

- Managed subagents now carry a structured agent-run context with real parent/root session lineage, and child workers are leaf by default: nested `agent` / `task_create(local_agent)` delegation is blocked unless the parent session explicitly has orchestration budget.
- `write_file` now waits for edit approval before creating missing parent directories, so rejected writes no longer leave empty folders behind.
- Default app logging now creates a process-stable timestamped file when `OPENHARNESS_LOG_FILE` is unset, avoids redundant startup rotation for those generated files, and still applies retention cleanup across older `openharness*.jsonl` runs.
- `cron_manager` now keeps create/update/enable operations non-blocking when the scheduler daemon is stopped, while returning an explicit `oh cron start` hint so saved jobs are not mistaken for active scheduling.
- Skill discovery and the runtime skills prompt now also load project-local skills from the launch directory's `.openharness/skills`, and prompt cache invalidation tracks those files so newly added workspace skills appear immediately.
- `bash` tool now runs without a PTY, injects non-interactive shell defaults (`GIT_PAGER=cat`, `PAGER=cat`, `MANPAGER=cat`, `GIT_TERMINAL_PROMPT=0`, `CI=1`), and preflights pager/editor-style commands like `git diff` without `--no-pager`, preventing React TUI sessions from appearing hung on `Running bash` while waiting on hidden terminal interaction.
- Skill loading now skips invalid `SKILL.md` entries when the directory name does not match frontmatter `name` or when no real description is provided, and logs each loaded or skipped skill with its outcome.
- React TUI keeps background-task activity visible with the animated prompt cue, elapsed timer, and compact dynamic status-bar cue, while avoiding foreground busy spinner churn and task metadata refreshes.
- React TUI no longer lets backend stderr warnings write directly to the terminal, and avoids reusing the welcome screen's scroll measurement for the first user turn, preventing a whole-screen flicker when sending the first message after startup.
- React TUI keeps the welcome banner in transcript scrollback after the first message, so users can scroll back to the startup context without it staying in the live tail.
- `image_generation` now provides a hand-written tool schema, avoiding the startup `UserWarning` from the default Pydantic schema fallback.
- Background `local_agent` tasks now launch the headless `--task-worker` mode instead of the React TUI, preventing Ink raw-mode failures when agents are spawned from TUI sessions.
- React TUI prompt footer now shows a single context-aware shortcut line instead of two dense help rows, keeping idle composition hints separate from busy-state run controls.
- React TUI now enables xterm bracketed paste mode and buffers pasted content into a single input event, so multi-line pastes preserve their original line layout, no longer drop earlier-typed characters, and never trip the submit shortcut on pasted carriage returns. Pasted CR/CRLF line endings are normalised to LF.
- React TUI multiline composer no longer drops earlier-typed characters when a multiline paste arrives mid-session, and avoids replaying already-buffered preview lines when paste data streams in one character at a time.
- React TUI multiline composer now submits buffered text even when the current line is empty after `Shift+Enter`, so users can end a multi-line draft with a blank cursor line and still send the message.
- React TUI `/skills` picker now supports in-modal keyboard filtering by skill name, so long skill lists can be narrowed down immediately without stepping through the full list with arrow keys.
- React TUI `/skills` picker now pre-fills the selected skill as `/<skill-name> ` in the composer and waits for the user query, matching the intended skill-invocation flow instead of immediately loading the skill on selection.
- React TUI `!shell-command` transcript rows no longer wrap command output in a bordered panel; the result body now renders inline with dimmed text to keep shell output lower-contrast than normal conversation text.
- React TUI slash autocomplete now includes direct `/<skill-name>` aliases alongside slash commands, so users can type a known skill prefix and complete or invoke it without opening the `/skills` picker first.
- React TUI select menus now window long option lists, show skill descriptions below names with wrapping indentation, and highlight the selected option.
- React TUI select menus now cycle on arrow-key and wheel navigation, so pickers like `/model` wrap from the last option back to the first (and vice versa).
- React TUI slash command picker now groups subcommands under their root command and previews them in a side submenu.
- React TUI slash command picker now supports cyclic browsing at both menu levels, lets `→` enter the subcommand column, and can prefill a combined `/<command> <subcommand> ` prompt from the submenu.
- React TUI question modal input now preserves Shift-modified printable keys from terminals that emit modifyOtherKeys/CSI-u sequences.
- Compaction now detects llama.cpp/OpenAI-compatible context overflow errors, accounts for image blocks in auto-compact token estimates, and strips image payloads from summarizer-only compaction requests.
- Large tool results are now bounded in conversation history: oversized outputs are saved under `tool_artifacts`, old MCP results become microcompactable, and context collapse trims stale tool-result payloads.
- ohmo now keeps personal memory isolated from OpenHarness project memory: `/memory` in ohmo sessions targets the ohmo workspace memory store, and ohmo runtime prompt refreshes no longer inject project memory unless explicitly requested.
- Bridge command (`/bridge`) is now local-only by default (`remote_invocable=False`) to prevent remote sessions from spawning bridge sub-sessions.
- `todo_write` tool now updates an existing unchecked item in-place when `checked=True` instead of appending a duplicate `[x]` line.
- QueryEngine now auto-continues once when a tool-follow-up turn ends with an empty assistant message, preventing React TUI sessions from appearing to stop early until the user manually sends another message.
- Full-auto OpenAI-compatible loops now drop empty assistant turns before injecting internal retry prompts (like `done` reminders), preventing providers such as DeepSeek from rejecting the next request with `Invalid assistant message: content or tool_calls must be set`.
- React TUI transcript now keeps the full session history navigable instead of permanently truncating to the most recent 40 items, and scrolling away from the bottom no longer gets pulled back by incoming output. The frontend now tracks an explicit transcript viewport, supports `PgUp` / `PgDn`, and listens for mouse-wheel scroll events in compatible terminals.

- React TUI spinner now stays visible throughout the entire agent turn: `assistant_complete` no longer resets `busy` state prematurely, and `tool_started` explicitly sets `busy=true` so the status bar remains active even when tool calls follow an assistant message. `line_complete` is the sole signal that ends the turn and clears the spinner.
- Skill loader now uses `yaml.safe_load` to parse SKILL.md frontmatter, correctly handling YAML block scalars (`>`, `|`), quoted values, and other standard YAML constructs instead of naive line-by-line splitting.
- `BackendHostConfig` was missing the `cwd` field, causing `AttributeError: 'BackendHostConfig' object has no attribute 'cwd'` on startup when `oh` was run after the runtime refactor that added `cwd` support to `build_runtime`.
- Shell-escape `$ARGUMENTS` substitution in command hooks to prevent shell injection from payload values containing metacharacters like `$(...)` or backticks.
- Swarm `_READ_ONLY_TOOLS` now uses actual registered tool names (snake_case) instead of PascalCase, fixing read-only auto-approval in `handle_permission_request`.
- Memory scanner now parses YAML frontmatter (`name`, `description`, `type`) instead of returning raw `---` as description.
- Memory search matches against body content in addition to metadata, with metadata weighted higher for relevance.
- Memory search tokenizer handles Han characters for multilingual queries.
- Fixed duplicate response in React TUI caused by double Enter key submission in the input handler.
- Fixed concurrent permission modals overwriting each other in TUI default mode when the LLM returns multiple tool calls in one response; `_ask_permission` now serialises callers via an `asyncio.Lock` so each modal is shown and resolved before the next one is emitted.
- Fixed React TUI Markdown tables to size columns from rendered cell text so inline formatting like code spans and bold text no longer breaks alignment.
- Fixed grep tool crashing with `ValueError` / `LimitOverrunError` when ripgrep outputs a line longer than 64 KB (e.g. minified assets or lock files). The asyncio subprocess stream limit is now 8 MB and oversized lines are skipped rather than terminating the session.
- Fixed React TUI exit leaving the shell prompt concatenated with the last TUI line. The terminal cleanup handler now writes a trailing newline (`\n`) alongside the cursor-show escape sequence so the shell prompt always starts on a fresh line.
- Fixed React TUI prompt paste handling so multi-character paste no longer overwrites already typed text, and buffered multiline preview lines now stay clipped to a single terminal row instead of wrapping and breaking prompt box alignment.
- Fixed React TUI multiline user transcript rendering after pasted code so the `you` label stays attached to the turn header and long pasted lines are clipped instead of wrapping into broken divider/role layouts.
- Fixed React TUI `/vim` mode so it now drives real modal editing in both the prompt composer and expanded editor, with normal/insert state, core Vim cursor motions, `o`/`O` open-line commands, and visible mode hints instead of being a status-only toggle.
- Fixed React TUI multiline prompt deletion so pressing Backspace at the start of the last line now pulls the previous buffered line back into the active editor, allowing cross-line deletion again after newline input.
- Reduced React TUI redraw pressure when `output_style=codex` by avoiding token-level assistant buffer flushes during streaming.

### Changed

- Dry-run output now reports a `ready` / `warning` / `blocked` readiness verdict, concrete `next_actions`, likely matching skills/tools for normal prompts, and richer slash-command previews for read-only vs stateful command paths.
- React TUI now groups consecutive `tool` + `tool_result` transcript rows into a single compound row: success shows the result line count inline (e.g. `→ 24L`), errors show a red icon and up to 5 lines of error detail beneath the tool row. Standalone successful tool results are suppressed to reduce transcript noise; standalone errors are still surfaced.
- README now links to contribution docs, changelog, showcase material, and provider compatibility guidance.
- README quick start now includes a one-command demo and clearer provider compatibility notes.
- README provider compatibility section updated to include OpenAI-format providers.
- `agent` is now the documented preferred API for managed subagent delegation, while `task_create(local_agent)` is described as a low-level compatibility path and reuses the same subprocess-backed spawn flow.

## [0.1.0] - 2026-04-01

### Added

- Initial public release of OpenHarness.
- Core agent loop, tool registry, permission system, hooks, skills, plugins, MCP support, and terminal UI.
