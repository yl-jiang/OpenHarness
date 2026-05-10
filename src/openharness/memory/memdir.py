"""Memory prompt helpers."""

from __future__ import annotations

from pathlib import Path

from openharness.memory.paths import (
    get_curated_memory_dir,
    get_memory_entrypoint,
    get_project_memory_dir,
)
from openharness.memory.store import MemoryStore


MEMORY_GUIDANCE = (
    "You have persistent memory across sessions. Save durable facts using the memory "
    "tool: user preferences, environment details, tool quirks, and stable conventions. "
    "Memory is injected into every turn, so keep it compact and focused on facts that "
    "will still matter later.\n"
    "Prioritize what reduces future user steering — the most valuable memory is one "
    "that prevents the user from having to correct or remind you again. "
    "User preferences and recurring corrections matter more than procedural task details.\n"
    "Do NOT save task progress, session outcomes, completed-work logs, or temporary TODO "
    "state to memory; use session_search to recall those from past transcripts. "
    "If you've discovered a new way to do something, solved a problem that could be "
    "necessary later, save it as a skill with the skill tool."
)


def load_memory_prompt(cwd: str | Path, *, max_entrypoint_lines: int = 200) -> str | None:
    """Return the memory prompt section for the current project."""
    memory_dir = get_project_memory_dir(cwd)
    curated_dir = get_curated_memory_dir(cwd)
    entrypoint = get_memory_entrypoint(cwd)
    lines = [
        "# Memory",
        (
            f"- Persistent memory directory: {memory_dir} "
            "(human-managed topic files plus the root MEMORY.md index; used by /memory)"
        ),
        (
            f"- Curated memory directory: {curated_dir} "
            "(tool-managed durable facts for USER.md and MEMORY.md prompt injection)"
        ),
        "- Store concise project notes and topical entries in the persistent memory directory.",
        "- Store compact, stable user or project facts in the curated memory directory via the memory tool.",
    ]

    if entrypoint.exists():
        content_lines = entrypoint.read_text(encoding="utf-8").splitlines()[:max_entrypoint_lines]
        if content_lines:
            lines.extend(["", "## MEMORY.md", "```md", *content_lines, "```"])
    else:
        lines.extend(
            [
                "",
                "## MEMORY.md",
                "(not created yet)",
            ]
        )

    store = MemoryStore(curated_dir)
    store.load_from_disk()
    curated_blocks = [
        block
        for block in (
            store.format_for_system_prompt("user"),
            store.format_for_system_prompt("memory"),
        )
        if block
    ]
    if curated_blocks:
        lines.extend(["", "## Curated Memory", *curated_blocks])

    return "\n".join(lines)
