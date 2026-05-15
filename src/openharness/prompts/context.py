"""Higher-level system prompt assembly."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from openharness.config.paths import (
    get_project_active_repo_context_path,
    get_project_issue_file,
    get_project_pr_comments_file,
)
from openharness.config.settings import Settings
from openharness.coordinator.coordinator_mode import get_coordinator_system_prompt, is_coordinator_mode
from openharness.memory import (
    find_relevant_memories,
    get_curated_memory_dir,
    get_memory_entrypoint,
    get_project_memory_dir,
    load_memory_prompt,
)
from openharness.personalization.rules import load_local_rules
from openharness.prompts.claudemd import discover_claude_md_files, load_claude_md_prompt
from openharness.services import estimate_tokens
from openharness.permissions.modes import PermissionMode
from openharness.prompts.system_prompt import build_system_prompt
from openharness.skills.loader import discover_project_skill_dirs, get_user_skill_dirs, load_skill_registry

SKILLS_GUIDANCE = (
    "# Skill guidance\n"
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manager so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manager(action='patch') — don't wait to be asked. "
    "Skills that aren't maintained become liabilities.\n"
)

TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "# Tool-use enforcement\n"
    "You MUST use your tools to take action — do not describe what you would do "
    "or plan to do without actually doing it. When you say you will perform an "
    "action (e.g. 'I will run the tests', 'Let me check the file', 'I will create "
    "the project'), you MUST immediately make the corresponding tool call in the same "
    "response. Never end your turn with a promise of future action — execute it now.\n"
    "Keep working until the task is actually complete. Do not stop with a summary of "
    "what you plan to do next time. If you have tools available that can accomplish "
    "the task, use them instead of telling the user what you would do.\n"
    "Every response should either (a) contain tool calls that make progress, or "
    "(b) deliver a final result to the user. Responses that only describe intentions "
    "without acting are not acceptable.\n"
)

# Prepended to user-provided instruction blocks (CLAUDE.md, local rules) to
# signal the shift from system defaults to user-controlled content and to
# establish conflict-resolution semantics via recency bias.
_CONTEXTUAL_INSTRUCTIONS_PREAMBLE = (
    "---\n"
    "The following instructions come from the user's project configuration "
    "and personal rules. In case of conflict with the system defaults above, "
    "**these project-level and personal rules take precedence**.\n"
    "---\n\n"
)

# Repeated at the absolute end of the system prompt (priority 50) so that the
# most important behavioral constraints benefit from both primacy (base prompt)
# and recency (final reminder) in the model's attention.
_FINAL_REMINDER = (
    "# Final Reminder\n"
    "Before every response, verify you are following these core rules:\n"
    "- **Inquiry vs Directive**: if the request is ambiguous, treat as an Inquiry—explain "
    "and propose, but do NOT modify files or run irreversible commands without explicit confirmation.\n"
    "- **3-Strike Reset**: if the same fix fails 3 times in a row, stop patching and propose "
    "a structurally different approach.\n"
    "- **Confirmation Protocol**: a denied tool call is final for that action—do not re-attempt "
    "or negotiate.\n"
    "- **Reversibility check**: before any destructive action (delete files, force-push, drop "
    "tables), ask the user to confirm.\n"
    "- **Scope discipline**: fix exactly what was asked. No opportunistic refactors, no "
    "unsolicited features."
)

# Maps PermissionMode to the mode label injected into the system prompt preamble.
_PERMISSION_MODE_LABELS: dict[PermissionMode, str] = {
    PermissionMode.DEFAULT: "Default",
    PermissionMode.PLAN: "Plan",
    PermissionMode.FULL_AUTO: "Auto",
}

_RUNTIME_SYSTEM_PROMPT_CACHE_SIZE = 32
_LOCAL_RULES_FILE = Path("~/.openharness/local_rules/rules.md").expanduser()
_FINGERPRINT_MAX_FILES = 400


@dataclass(frozen=True)
class PromptBlock:
    """A named section of the runtime system prompt."""

    id: str
    title: str
    content: str
    priority: int = 100
    source: str = "runtime"
    cacheable: bool = True


@dataclass(frozen=True)
class AgentPromptProfile:
    """Prompt-level switches for different agent roles."""

    name: str
    role_prompt: str = ""
    include_tool_enforcement: bool = True
    include_delegation: bool = True
    include_skills: bool = True


DEFAULT_AGENT_PROMPT_PROFILE = AgentPromptProfile(name="default")
PLAN_AGENT_PROMPT_PROFILE = AgentPromptProfile(
    name="plan",
    role_prompt=(
        "# Agent Profile\n"
        "You are in planning mode. Focus on analysis and implementation plans. "
        "Do not modify files unless the user explicitly asks you to implement."
    ),
)
COMPACT_AGENT_PROMPT_PROFILE = AgentPromptProfile(
    name="compact",
    role_prompt=(
        "# Agent Profile\n"
        "You are in compact summary mode. Summarize the conversation into structured state. Do not use tools."
    ),
    include_tool_enforcement=False,
    include_delegation=False,
    include_skills=False,
)
_BUILTIN_AGENT_PROMPT_PROFILES = {
    DEFAULT_AGENT_PROMPT_PROFILE.name: DEFAULT_AGENT_PROMPT_PROFILE,
    PLAN_AGENT_PROMPT_PROFILE.name: PLAN_AGENT_PROMPT_PROFILE,
    COMPACT_AGENT_PROMPT_PROFILE.name: COMPACT_AGENT_PROMPT_PROFILE,
}


def _build_skills_section(
    cwd: str | Path,
    *,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    settings: Settings | None = None,
) -> str | None:
    """Build a system prompt section listing available skills."""
    registry = load_skill_registry(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    skills = [skill for skill in registry.list_skills() if not skill.disable_model_invocation]
    if not skills:
        return None
    lines = [SKILLS_GUIDANCE] + [
        "# Available Skills",
        "",
        "The following skills are available via the `skill_manager` tool. "
        "When a user's request matches a skill, invoke it with "
        "`skill_manager(action=\"load\", name=\"<skill_name>\")` "
        "to load detailed instructions before proceeding.",
        "",
    ]
    for skill in skills:
        lines.append(f"- **{skill.name}**: {skill.description}")
    return "\n".join(lines)


def _build_delegation_section() -> str:
    """Build a concise section describing delegation and worker usage."""
    return "\n".join(
        [
            "# Delegation And Subagents",
            "",
            "OpenHarness can delegate background work with the `agent` tool.",
            "Use it when the user explicitly asks for a subagent, background worker, or parallel investigation, "
            "or when the task clearly benefits from splitting off a focused worker.",
            "",
            "Default pattern:",
            '- Spawn with `agent(description=..., prompt=..., subagent_type=\"worker\")`.',
            "- Inspect running or recorded workers with `/agents`.",
            "- Inspect one worker in detail with `/agents show TASK_ID`.",
            "- Send follow-up instructions with `send_message(task_id=..., message=...)`.",
            "- Read worker output with `task_output(task_id=...)`.",
            "",
            "Prefer a normal direct answer for simple tasks. Use subagents only when they materially help.",
        ]
    )


def build_runtime_system_prompt(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    include_project_memory: bool = True,
    agent_profile: str | AgentPromptProfile = "default",
) -> str:
    """Build the runtime system prompt with project instructions and memory."""
    return render_prompt_blocks(
        build_runtime_prompt_blocks(
            settings,
            cwd=cwd,
            latest_user_prompt=latest_user_prompt,
            extra_skill_dirs=extra_skill_dirs,
            extra_plugin_roots=extra_plugin_roots,
            include_project_memory=include_project_memory,
            agent_profile=agent_profile,
        )
    )


def build_runtime_prompt_blocks(
    settings: Settings,
    *,
    cwd: str | Path,
    latest_user_prompt: str | None = None,
    extra_skill_dirs: Iterable[str | Path] | None = None,
    extra_plugin_roots: Iterable[str | Path] | None = None,
    include_project_memory: bool = True,
    agent_profile: str | AgentPromptProfile = "default",
) -> tuple[PromptBlock, ...]:
    """Build named runtime prompt blocks for rendering or diagnostics."""
    resolved_cwd = str(Path(cwd).expanduser().resolve())
    settings_key = _settings_cache_key(settings)
    profile = _resolve_agent_prompt_profile(agent_profile)
    profile_key = _agent_profile_cache_key(profile)
    extra_skill_dirs_key = _normalize_path_tuple(extra_skill_dirs)
    extra_plugin_roots_key = _normalize_path_tuple(extra_plugin_roots)
    coordinator_mode = is_coordinator_mode()
    dependency_fingerprint = _runtime_prompt_dependency_fingerprint(
        resolved_cwd,
        settings=settings,
        extra_skill_dirs=extra_skill_dirs_key,
        extra_plugin_roots=extra_plugin_roots_key,
        include_project_memory=include_project_memory,
        coordinator_mode=coordinator_mode,
    )
    return _build_runtime_prompt_blocks_cached(
        settings_key,
        profile_key,
        resolved_cwd,
        latest_user_prompt or "",
        extra_skill_dirs_key,
        extra_plugin_roots_key,
        include_project_memory,
        coordinator_mode,
        dependency_fingerprint,
    )


def clear_runtime_system_prompt_cache() -> None:
    """Clear cached runtime system prompt entries."""

    _build_runtime_prompt_blocks_cached.cache_clear()


@lru_cache(maxsize=_RUNTIME_SYSTEM_PROMPT_CACHE_SIZE)
def _build_runtime_prompt_blocks_cached(
    settings_key: str,
    profile_key: str,
    cwd: str,
    latest_user_prompt: str,
    extra_skill_dirs: tuple[str, ...],
    extra_plugin_roots: tuple[str, ...],
    include_project_memory: bool,
    coordinator_mode: bool,
    dependency_fingerprint: tuple[tuple[str, int | None, int | None], ...],
) -> tuple[PromptBlock, ...]:
    del dependency_fingerprint
    settings = Settings.model_validate(json.loads(settings_key))
    profile = _agent_profile_from_cache_key(profile_key)
    latest_prompt = latest_user_prompt or None
    blocks: list[PromptBlock] = []

    if coordinator_mode:
        blocks.append(
            PromptBlock(
                id="coordinator-system",
                title="Coordinator System Prompt",
                content=get_coordinator_system_prompt(),
                priority=1000,
                source="coordinator",
            )
        )
    elif settings.system_prompt is None:
        blocks.append(
            PromptBlock(
                id="base-system",
                title="Base System Prompt",
                content=build_system_prompt(
                    cwd=str(cwd),
                    mode_label=_PERMISSION_MODE_LABELS.get(settings.permission.mode),
                ),
                priority=1000,
                source="system",
            )
        )
    else:
        blocks.append(
            PromptBlock(
                id="base-system",
                title="Base System Prompt",
                content=build_system_prompt(custom_prompt=settings.system_prompt, cwd=str(cwd)),
                priority=1000,
                source="system",
            )
        )

    if profile.role_prompt and not coordinator_mode:
        blocks.append(
            PromptBlock(
                id=f"agent-profile:{profile.name}",
                title=f"Agent Profile: {profile.name}",
                content=profile.role_prompt,
                priority=980,
                source="agent_profile",
            )
        )

    if settings.fast_mode:
        blocks.append(
            PromptBlock(
                id="session-mode",
                title="Session Mode",
                content="# Session Mode\nFast mode is enabled. Prefer concise replies, minimal tool use, and quicker progress over exhaustive exploration.",
                priority=900,
                source="settings",
            )
        )

    blocks.append(
        PromptBlock(
            id="reasoning-settings",
            title="Reasoning Settings",
            content=(
                "# Reasoning Settings\n"
                f"- Effort: {settings.effort}\n"
                f"- Passes: {settings.passes}\n"
                "Adjust depth and iteration count to match these settings while still completing the task."
            ),
            priority=850,
            source="settings",
        )
    )

    skills_section = _build_skills_section(
        cwd,
        extra_skill_dirs=extra_skill_dirs,
        extra_plugin_roots=extra_plugin_roots,
        settings=settings,
    )
    if skills_section and not coordinator_mode and profile.include_skills:
        blocks.append(
            PromptBlock(
                id="available-skills",
                title="Available Skills",
                content=skills_section,
                priority=650,
                source="skills",
            )
        )

    if not coordinator_mode and profile.include_delegation:
        blocks.append(
            PromptBlock(
                id="delegation",
                title="Delegation And Subagents",
                content=_build_delegation_section(),
                priority=700,
                source="runtime",
            )
        )

    if profile.include_tool_enforcement:
        blocks.append(
            PromptBlock(
                id="tool-use-enforcement",
                title="Tool-use enforcement",
                content=TOOL_USE_ENFORCEMENT_GUIDANCE,
                priority=950,
                source="runtime",
            )
        )

    claude_md = load_claude_md_prompt(cwd)
    if claude_md:
        blocks.append(
            PromptBlock(
                id="project-instructions",
                title="Project Instructions",
                content=_CONTEXTUAL_INSTRUCTIONS_PREAMBLE + claude_md,
                priority=200,
                source="CLAUDE.md",
            )
        )

    local_rules = load_local_rules()
    if local_rules:
        blocks.append(
            PromptBlock(
                id="local-rules",
                title="Local Rules",
                content=_CONTEXTUAL_INSTRUCTIONS_PREAMBLE + local_rules,
                priority=190,
                source="local_rules",
            )
        )

    for block_id, title, path in (
        ("issue-context", "Issue Context", get_project_issue_file(cwd)),
        ("pr-comments", "Pull Request Comments", get_project_pr_comments_file(cwd)),
        ("active-repo-context", "Active Repo Context", get_project_active_repo_context_path(cwd)),
    ):
        if path.exists():
            content = path.read_text(encoding="utf-8", errors="replace").strip()
            if content:
                blocks.append(
                    PromptBlock(
                        id=block_id,
                        title=title,
                        content=f"# {title}\n\n```md\n{content[:12000]}\n```",
                        priority=180,
                        source="project_context",
                    )
                )

    if include_project_memory and settings.memory.enabled:
        memory_section = load_memory_prompt(
            cwd,
            max_entrypoint_lines=settings.memory.max_entrypoint_lines,
        )
        if memory_section:
            blocks.append(
                PromptBlock(
                    id="memory",
                    title="Memory",
                    content=memory_section,
                    priority=120,
                    source="memory",
                )
            )

        if latest_prompt:
            relevant = find_relevant_memories(
                latest_prompt,
                cwd,
                max_results=settings.memory.max_files,
            )
            if relevant:
                lines = ["# Relevant Memories"]
                for header in relevant:
                    content = header.path.read_text(encoding="utf-8", errors="replace").strip()
                    lines.extend(
                        [
                            "",
                            f"## {header.path.name}",
                            "```md",
                            content[:8000],
                            "```",
                        ]
                    )
                blocks.append(
                    PromptBlock(
                        id="relevant-memories",
                        title="Relevant Memories",
                        content="\n".join(lines),
                        priority=110,
                        source="memory",
                    )
                )

    if profile.include_tool_enforcement and not coordinator_mode:
        blocks.append(
            PromptBlock(
                id="final-reminder",
                title="Final Reminder",
                content=_FINAL_REMINDER,
                priority=50,
                source="system",
            )
        )

    return tuple(dedupe_prompt_blocks(blocks))


def dedupe_prompt_blocks(blocks: Iterable[PromptBlock]) -> list[PromptBlock]:
    """Return prompt blocks with duplicate ids removed, preserving first occurrence."""

    seen_ids: set[str] = set()
    deduped: list[PromptBlock] = []
    for block in blocks:
        if not block.content.strip() or block.id in seen_ids:
            continue
        seen_ids.add(block.id)
        deduped.append(block)
    return deduped


def render_prompt_blocks(blocks: Iterable[PromptBlock]) -> str:
    """Render prompt blocks into the system prompt text sent to the model.

    Blocks are sorted by priority (descending) so higher-priority system
    instructions always appear before lower-priority user-customizable content,
    regardless of insertion order.
    """
    sorted_blocks = sorted(dedupe_prompt_blocks(blocks), key=lambda b: -b.priority)
    return "\n\n".join(block.content.strip() for block in sorted_blocks)


def format_prompt_blocks_debug(blocks: Iterable[PromptBlock]) -> str:
    """Return a compact diagnostic view of prompt block composition."""

    deduped = sorted(
        dedupe_prompt_blocks(blocks),
        key=lambda block: (-block.priority, block.id),
    )
    lines = ["Runtime prompt blocks:"]
    if not deduped:
        lines.append("(none)")
        return "\n".join(lines)

    rows = [
        (
            block.id,
            str(len(block.content)),
            str(estimate_tokens(block.content)),
            str(block.priority),
            block.source,
            str(block.cacheable).lower(),
        )
        for block in deduped
    ]
    headers = ("ID", "CHARS", "TOKENS", "PRIORITY", "SOURCE", "CACHEABLE")
    widths = [
        max(len(headers[0]), *(len(row[0]) for row in rows)),
        max(len(headers[1]), *(len(row[1]) for row in rows)),
        max(len(headers[2]), *(len(row[2]) for row in rows)),
        max(len(headers[3]), *(len(row[3]) for row in rows)),
        max(len(headers[4]), *(len(row[4]) for row in rows)),
        max(len(headers[5]), *(len(row[5]) for row in rows)),
    ]
    lines.append(
        (
            f"{headers[0]:<{widths[0]}}  "
            f"{headers[1]:>{widths[1]}}  "
            f"{headers[2]:>{widths[2]}}  "
            f"{headers[3]:>{widths[3]}}  "
            f"{headers[4]:<{widths[4]}}  "
            f"{headers[5]:<{widths[5]}}"
        ).rstrip()
    )
    for row in rows:
        lines.append(
            (
                f"{row[0]:<{widths[0]}}  "
                f"{row[1]:>{widths[1]}}  "
                f"{row[2]:>{widths[2]}}  "
                f"{row[3]:>{widths[3]}}  "
                f"{row[4]:<{widths[4]}}  "
                f"{row[5]:<{widths[5]}}"
            ).rstrip()
        )
    return "\n".join(lines)


def _resolve_agent_prompt_profile(agent_profile: str | AgentPromptProfile) -> AgentPromptProfile:
    if isinstance(agent_profile, AgentPromptProfile):
        return agent_profile
    try:
        return _BUILTIN_AGENT_PROMPT_PROFILES[agent_profile]
    except KeyError as exc:
        allowed = ", ".join(sorted(_BUILTIN_AGENT_PROMPT_PROFILES))
        raise ValueError(f"Unknown agent prompt profile: {agent_profile}. Expected one of: {allowed}") from exc


def _agent_profile_cache_key(profile: AgentPromptProfile) -> str:
    return json.dumps(
        {
            "name": profile.name,
            "role_prompt": profile.role_prompt,
            "include_tool_enforcement": profile.include_tool_enforcement,
            "include_delegation": profile.include_delegation,
            "include_skills": profile.include_skills,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _agent_profile_from_cache_key(profile_key: str) -> AgentPromptProfile:
    data = json.loads(profile_key)
    return AgentPromptProfile(
        name=data["name"],
        role_prompt=data.get("role_prompt", ""),
        include_tool_enforcement=data.get("include_tool_enforcement", True),
        include_delegation=data.get("include_delegation", True),
        include_skills=data.get("include_skills", True),
    )


def _settings_cache_key(settings: Settings) -> str:
    return json.dumps(
        settings.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalize_path_tuple(paths: Iterable[str | Path] | None) -> tuple[str, ...]:
    if paths is None:
        return ()
    return tuple(str(Path(path).expanduser().resolve()) for path in paths)


def _runtime_prompt_dependency_fingerprint(
    cwd: str,
    *,
    settings: Settings,
    extra_skill_dirs: tuple[str, ...],
    extra_plugin_roots: tuple[str, ...],
    include_project_memory: bool,
    coordinator_mode: bool,
) -> tuple[tuple[str, int | None, int | None], ...]:
    root = Path(cwd)
    paths: list[Path] = [
        *discover_claude_md_files(root),
        get_project_issue_file(root),
        get_project_pr_comments_file(root),
        get_project_active_repo_context_path(root),
        _LOCAL_RULES_FILE,
    ]
    if not coordinator_mode:
        for directory in get_user_skill_dirs():
            paths.extend(_iter_prompt_dependency_files(directory))
        if settings.allow_project_skills:
            for directory in discover_project_skill_dirs(root, settings.project_skill_dirs):
                paths.extend(_iter_prompt_dependency_files(directory))
        for directory in extra_skill_dirs:
            paths.extend(_iter_prompt_dependency_files(Path(directory)))
        for directory in extra_plugin_roots:
            paths.extend(_iter_prompt_dependency_files(Path(directory)))
    if include_project_memory and settings.memory.enabled:
        paths.append(get_memory_entrypoint(root))
        paths.extend(_iter_prompt_dependency_files(get_project_memory_dir(root)))
        paths.extend(_iter_prompt_dependency_files(get_curated_memory_dir(root)))
    return tuple(_path_signature(path) for path in _dedupe_paths(paths))


def _iter_prompt_dependency_files(root: Path) -> list[Path]:
    if not root.exists():
        return [root]
    if root.is_file():
        return [root]
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if len(files) >= _FINGERPRINT_MAX_FILES:
            break
        if path.is_file() and path.suffix.lower() in {".md", ".json", ".yaml", ".yml"}:
            files.append(path)
    return files or [root]


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[Path] = set()
    deduped: list[Path] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped


def _path_signature(path: Path) -> tuple[str, int | None, int | None]:
    try:
        stat = path.stat()
    except OSError:
        return str(path), None, None
    return str(path), stat.st_mtime_ns, stat.st_size
