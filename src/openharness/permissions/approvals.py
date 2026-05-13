"""Session-local approval coordination for OpenHarness.

Architecture
------------
``ApprovalCoordinator`` is the single entry point for all approval decisions:

* **Policy** stays in ``PermissionChecker`` — it decides what *would* require
  confirmation based on settings, tool name, path, and command.
* **Memory** lives in ``ApprovalState`` — it tracks what the user has already
  approved for this process lifetime.
* **Prompting** is delegated to an injected ``prompt_fn`` — the UI layer
  (backend host or textual app) provides this.

All tools, including file-mutating ones, go through ``authorize_tool``.  The
pipeline can pre-compute a diff preview (via ``BaseTool.compute_preview``) and
pass it in; the coordinator embeds it in the ``ApprovalRequest`` so the UI can
show the diff as part of the same single approval interaction.
"""

from __future__ import annotations

import asyncio
import fnmatch
from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Literal

from openharness.permissions.checker import PermissionChecker, PermissionDecision
from openharness.utils.log import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

ApprovalReply = Literal["once", "always", "reject"]


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApprovalRequest:
    """Structured request for one user-approval interaction."""

    kind: str  # "tool"
    tool_name: str = ""
    reason: str = ""
    path: str = ""
    diff: str = ""
    added: int = 0
    removed: int = 0


@dataclass(frozen=True)
class ApprovalRule:
    """A session-local remembered approval rule."""

    kind: str
    permission: str = ""
    pattern: str = ""
    scope: str = ""


# ---------------------------------------------------------------------------
# In-memory approval state
# ---------------------------------------------------------------------------


class ApprovalState:
    """Holds remembered approval rules for the current process lifetime."""

    def __init__(self) -> None:
        self._rules: list[ApprovalRule] = []

    def match(self, query: ApprovalRule) -> bool:
        """Return True if any stored rule covers *query*.

        Matching logic:
        - ``kind`` must be equal.
        - If the stored rule has a non-empty ``permission``, it must fnmatch the
          query's ``permission`` (stored rule is the glob, query is the value).
        - ``pattern`` must fnmatch the query's ``pattern`` in the same way.
        """
        for r in self._rules:
            if r.kind != query.kind:
                continue
            if r.permission and not fnmatch.fnmatch(query.permission, r.permission):
                continue
            if not fnmatch.fnmatch(query.pattern, r.pattern):
                continue
            return True
        return False

    def remember(self, rule: ApprovalRule) -> None:
        """Add *rule* to permanent approvals if not already present."""
        if rule not in self._rules:
            self._rules.append(rule)


# ---------------------------------------------------------------------------
# Prompt function type
# ---------------------------------------------------------------------------

PromptFn = Callable[[ApprovalRequest], Awaitable[str]]
NotifyFn = Callable[[ApprovalRequest], Awaitable[None]]


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class ApprovalCoordinator:
    """Single orchestration point for all approval decisions.

    Usage
    -----
    * Call ``authorize_tool(...)`` from the tool-permission pipeline stage.
      Pass ``preview=(diff, added, removed)`` when a diff is available so the
      user sees the changes as part of the same approval interaction.
    * Call ``set_checker(...)`` to replace the underlying ``PermissionChecker``
      (e.g. after a ``/permissions`` mode change) without losing approval state.
    """

    def __init__(
        self,
        checker: PermissionChecker,
        *,
        prompt_fn: PromptFn | None = None,
        notify_fn: NotifyFn | None = None,
    ) -> None:
        self._checker = checker
        self._state = ApprovalState()
        self._lock: asyncio.Lock | None = None
        self._notify_fn = notify_fn
        self._prompt_fn: PromptFn | None = prompt_fn

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def checker(self) -> PermissionChecker:
        return self._checker

    @property
    def state(self) -> ApprovalState:
        return self._state

    def set_checker(self, checker: PermissionChecker) -> None:
        """Replace the permission checker without clearing approval state."""
        self._checker = checker

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def _approval_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def authorize_tool(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
        preview: tuple[str, int, int] | None = None,
    ) -> PermissionDecision:
        """Decide whether *tool_name* may run.

        Parameters
        ----------
        preview:
            Optional ``(diff_text, added_lines, removed_lines)`` computed by the
            tool's ``compute_preview`` method.  When provided the diff is embedded
            in the ``ApprovalRequest`` so the UI can show it inside the same
            approval interaction, with no separate prompting step.
        """
        decision = self._checker.evaluate(
            tool_name,
            is_read_only=is_read_only,
            file_path=file_path,
            command=command,
        )

        if decision.allowed:
            return decision

        if not decision.requires_confirmation:
            # Hard deny — no prompt
            return decision

        # Prompt under lock (serialise concurrent tool calls)
        async with self._approval_lock:
            # Re-check remembered state after acquiring the lock — a concurrent
            # call may have already remembered this permission.
            patterns = decision.always_patterns or decision.patterns
            if patterns and all(
                self._state.match(
                    ApprovalRule(kind="tool", permission=decision.permission, pattern=p)
                )
                for p in patterns
            ):
                return PermissionDecision(
                    allowed=True,
                    reason=f"{decision.permission} matches a remembered allow rule for this session",
                    permission=decision.permission,
                    patterns=decision.patterns,
                    always_patterns=decision.always_patterns,
                )

            if self._prompt_fn is None:
                return decision

            diff_text, added, removed = preview if preview else ("", 0, 0)
            request = ApprovalRequest(
                kind="tool",
                tool_name=tool_name,
                reason=decision.reason,
                path=file_path or "",
                diff=diff_text,
                added=added,
                removed=removed,
            )
            if self._notify_fn is not None:
                await self._notify_fn(request)
            raw_reply = await self._prompt_fn(request)
            reply = _normalize_reply(raw_reply)

            if reply == "reject":
                return PermissionDecision(
                    allowed=False,
                    requires_confirmation=False,
                    reason=decision.reason or f"Permission denied for {tool_name}",
                    permission=decision.permission,
                    patterns=decision.patterns,
                    always_patterns=decision.always_patterns,
                )

            if reply == "always":
                permission = decision.permission.strip()
                for raw_pattern in (decision.always_patterns or decision.patterns):
                    normalized = raw_pattern.strip()
                    if normalized:
                        self._state.remember(
                            ApprovalRule(kind="tool", permission=permission, pattern=normalized)
                        )

            return PermissionDecision(
                allowed=True,
                reason=f"Approved by user ({reply})",
                permission=decision.permission,
                patterns=decision.patterns,
                always_patterns=decision.always_patterns,
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_reply(raw: object) -> str:
    """Normalise a raw permission/approval reply to one of once/always/reject."""
    text = (str(raw) if raw is not None else "").strip().lower()
    if text in ("once", "always", "reject"):
        return text
    # Backward compat: truthy → "once", falsy → "reject"
    return "once" if raw else "reject"
