"""Curated file-backed memory store."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from openharness.utils.file_lock import exclusive_file_lock
from openharness.utils.fs import atomic_write_text

MemoryTarget = Literal["memory", "user"]

ENTRY_DELIMITER = "\n§\n"

_MEMORY_THREAT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"you\s+are\s+now\s+", "role_hijack"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (
        r"act\s+as\s+(if|though)\s+you\s+(have\s+no|don't\s+have)\s+(restrictions|limits|rules)",
        "bypass_restrictions",
    ),
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", "read_secrets"),
    (r"authorized_keys", "ssh_backdoor"),
    (r"\$HOME/\.ssh|\~/\.ssh", "ssh_access"),
    (r"\$HOME/\.openharness/\.env|\~/\.openharness/\.env", "openharness_env"),
)

_INVISIBLE_CHARS = frozenset(
    {
        "\u200b",
        "\u200c",
        "\u200d",
        "\u2060",
        "\ufeff",
        "\u202a",
        "\u202b",
        "\u202c",
        "\u202d",
        "\u202e",
    }
)


@dataclass(frozen=True)
class MemoryOperationResult:
    """Result returned by memory mutations and reads."""

    success: bool
    target: MemoryTarget
    entries: list[str] = field(default_factory=list)
    usage: str = ""
    message: str | None = None
    error: str | None = None
    matches: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "success": self.success,
            "target": self.target,
            "entries": self.entries,
            "usage": self.usage,
            "entry_count": len(self.entries),
        }
        if self.message:
            payload["message"] = self.message
        if self.error:
            payload["error"] = self.error
        if self.matches:
            payload["matches"] = self.matches
        return payload


def scan_memory_content(content: str) -> str | None:
    """Return a blocking error when memory content looks unsafe."""
    for char in _INVISIBLE_CHARS:
        if char in content:
            return (
                "Blocked: content contains invisible unicode character "
                f"U+{ord(char):04X} (possible injection)."
            )

    for pattern, pattern_id in _MEMORY_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            return (
                f"Blocked: content matches threat pattern '{pattern_id}'. "
                "Memory entries are injected into the system prompt and must not contain "
                "injection or exfiltration payloads."
            )

    return None


class MemoryStore:
    """Bounded curated memory persisted as MEMORY.md and USER.md files."""

    def __init__(
        self,
        memory_dir: str | Path,
        *,
        memory_char_limit: int = 2200,
        user_char_limit: int = 1375,
    ) -> None:
        self.memory_dir = Path(memory_dir)
        self.memory_char_limit = memory_char_limit
        self.user_char_limit = user_char_limit
        self.memory_entries: list[str] = []
        self.user_entries: list[str] = []
        self._system_prompt_snapshot: dict[MemoryTarget, str] = {"memory": "", "user": ""}

    def load_from_disk(self) -> None:
        """Load memory files and freeze the prompt snapshot for this session."""
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.memory_entries = _dedupe(self._read_file(self._path_for("memory")))
        self.user_entries = _dedupe(self._read_file(self._path_for("user")))
        self._system_prompt_snapshot = {
            "memory": self._render_block("memory", self.memory_entries),
            "user": self._render_block("user", self.user_entries),
        }

    def add(self, target: MemoryTarget, content: str) -> MemoryOperationResult:
        """Append a new entry unless it is empty, unsafe, duplicated, or over budget."""
        content = content.strip()
        if not content:
            return self._failure(target, "Content cannot be empty.")

        scan_error = scan_memory_content(content)
        if scan_error:
            return self._failure(target, scan_error)

        with exclusive_file_lock(self._lock_path(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            if content in entries:
                return self._success(target, "Entry already exists.")

            new_entries = entries + [content]
            new_total = len(ENTRY_DELIMITER.join(new_entries))
            limit = self._char_limit(target)
            if new_total > limit:
                current = self._char_count(target)
                return self._failure(
                    target,
                    (
                        f"Memory at {current:,}/{limit:,} chars. Adding this entry "
                        f"({len(content)} chars) would exceed the limit. Replace or remove "
                        "existing entries first."
                    ),
                )

            entries.append(content)
            self._set_entries(target, entries)
            self._save_target(target)

        return self._success(target, "Entry added.")

    def replace(
        self,
        target: MemoryTarget,
        old_text: str,
        new_content: str,
    ) -> MemoryOperationResult:
        """Replace the single entry containing ``old_text``."""
        old_text = old_text.strip()
        new_content = new_content.strip()
        if not old_text:
            return self._failure(target, "old_text cannot be empty.")
        if not new_content:
            return self._failure(target, "new_content cannot be empty. Use remove to delete entries.")

        scan_error = scan_memory_content(new_content)
        if scan_error:
            return self._failure(target, scan_error)

        with exclusive_file_lock(self._lock_path(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(idx, entry) for idx, entry in enumerate(entries) if old_text in entry]
            if not matches:
                return self._failure(target, f"No entry matched '{old_text}'.")
            if len(matches) > 1:
                return self._failure(
                    target,
                    f"Multiple entries matched '{old_text}'. Be more specific.",
                    matches=[_preview(entry) for _, entry in matches],
                )

            idx = matches[0][0]
            candidate = entries.copy()
            candidate[idx] = new_content
            new_total = len(ENTRY_DELIMITER.join(candidate))
            limit = self._char_limit(target)
            if new_total > limit:
                return self._failure(
                    target,
                    (
                        f"Replacement would put memory at {new_total:,}/{limit:,} chars. "
                        "Shorten the new content or remove other entries first."
                    ),
                )

            entries[idx] = new_content
            self._set_entries(target, entries)
            self._save_target(target)

        return self._success(target, "Entry replaced.")

    def remove(self, target: MemoryTarget, old_text: str) -> MemoryOperationResult:
        """Remove the single entry containing ``old_text``."""
        old_text = old_text.strip()
        if not old_text:
            return self._failure(target, "old_text cannot be empty.")

        with exclusive_file_lock(self._lock_path(target)):
            self._reload_target(target)
            entries = self._entries_for(target)
            matches = [(idx, entry) for idx, entry in enumerate(entries) if old_text in entry]
            if not matches:
                return self._failure(target, f"No entry matched '{old_text}'.")
            if len(matches) > 1:
                return self._failure(
                    target,
                    f"Multiple entries matched '{old_text}'. Be more specific.",
                    matches=[_preview(entry) for _, entry in matches],
                )

            entries.pop(matches[0][0])
            self._set_entries(target, entries)
            self._save_target(target)

        return self._success(target, "Entry removed.")

    def read(self, target: MemoryTarget) -> MemoryOperationResult:
        """Return the live entries for ``target``."""
        with exclusive_file_lock(self._lock_path(target)):
            self._reload_target(target)
        return self._success(target)

    def format_for_system_prompt(self, target: MemoryTarget) -> str | None:
        """Return the frozen prompt snapshot captured by ``load_from_disk``."""
        block = self._system_prompt_snapshot.get(target, "")
        return block or None

    def _path_for(self, target: MemoryTarget) -> Path:
        if target == "user":
            return self.memory_dir / "USER.md"
        if target == "memory":
            return self.memory_dir / "MEMORY.md"
        raise ValueError(f"Invalid memory target: {target}")

    def _lock_path(self, target: MemoryTarget) -> Path:
        path = self._path_for(target)
        return path.with_suffix(path.suffix + ".lock")

    def _entries_for(self, target: MemoryTarget) -> list[str]:
        return self.user_entries if target == "user" else self.memory_entries

    def _set_entries(self, target: MemoryTarget, entries: list[str]) -> None:
        if target == "user":
            self.user_entries = entries
        else:
            self.memory_entries = entries

    def _reload_target(self, target: MemoryTarget) -> None:
        self._set_entries(target, _dedupe(self._read_file(self._path_for(target))))

    def _save_target(self, target: MemoryTarget) -> None:
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self._path_for(target), ENTRY_DELIMITER.join(self._entries_for(target)))

    def _char_limit(self, target: MemoryTarget) -> int:
        return self.user_char_limit if target == "user" else self.memory_char_limit

    def _char_count(self, target: MemoryTarget) -> int:
        entries = self._entries_for(target)
        return len(ENTRY_DELIMITER.join(entries)) if entries else 0

    def _usage(self, target: MemoryTarget) -> str:
        current = self._char_count(target)
        limit = self._char_limit(target)
        pct = min(100, int((current / limit) * 100)) if limit > 0 else 0
        return f"{pct}% - {current:,}/{limit:,} chars"

    def _success(self, target: MemoryTarget, message: str | None = None) -> MemoryOperationResult:
        return MemoryOperationResult(
            success=True,
            target=target,
            entries=list(self._entries_for(target)),
            usage=self._usage(target),
            message=message,
        )

    def _failure(
        self,
        target: MemoryTarget,
        error: str,
        *,
        matches: list[str] | None = None,
    ) -> MemoryOperationResult:
        return MemoryOperationResult(
            success=False,
            target=target,
            entries=list(self._entries_for(target)),
            usage=self._usage(target),
            error=error,
            matches=matches or [],
        )

    def _render_block(self, target: MemoryTarget, entries: list[str]) -> str:
        if not entries:
            return ""
        header = (
            f"USER PROFILE (who the user is) [{self._usage(target)}]"
            if target == "user"
            else f"MEMORY (your personal notes) [{self._usage(target)}]"
        )
        separator = "=" * 46
        return f"{separator}\n{header}\n{separator}\n{ENTRY_DELIMITER.join(entries)}"

    @staticmethod
    def _read_file(path: Path) -> list[str]:
        if not path.exists():
            return []
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return []
        if not raw.strip():
            return []
        return [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]


def _dedupe(entries: list[str]) -> list[str]:
    return list(dict.fromkeys(entries))


def _preview(entry: str) -> str:
    return entry[:80] + ("..." if len(entry) > 80 else "")
