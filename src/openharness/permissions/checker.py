"""Permission checking for tool execution."""

from __future__ import annotations

import fnmatch
import shlex
from dataclasses import dataclass

from openharness.config.settings import PermissionSettings
from openharness.permissions.constants import (
    INSTALL_MARKERS,
    SAFE_BASH_ALWAYS_PREFIXES,
    SAFE_SINGLE_WORD_COMMANDS,
    SENSITIVE_PATH_PATTERNS,
)
from openharness.permissions.modes import PermissionMode
from openharness.utils.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class PermissionDecision:
    """Result of checking whether a tool invocation may run."""

    allowed: bool
    requires_confirmation: bool = False
    reason: str = ""
    permission: str = ""
    patterns: tuple[str, ...] = ()
    always_patterns: tuple[str, ...] = ()


@dataclass(frozen=True)
class PathRule:
    """A glob-based path permission rule."""

    pattern: str
    allow: bool  # True = allow, False = deny


@dataclass(frozen=True)
class RememberedAllowRule:
    """An in-memory allow rule approved by the user for this session."""

    permission: str
    pattern: str


class PermissionChecker:
    """Evaluate tool usage against the configured permission mode and rules."""

    def __init__(self, settings: PermissionSettings) -> None:
        self._settings = settings
        self._remembered_allow_rules: list[RememberedAllowRule] = []
        # Parse path rules from settings
        self._path_rules: list[PathRule] = []
        for rule in getattr(settings, "path_rules", []):
            pattern = getattr(rule, "pattern", None) or (
                rule.get("pattern") if isinstance(rule, dict) else None
            )
            allow = (
                getattr(rule, "allow", True)
                if not isinstance(rule, dict)
                else rule.get("allow", True)
            )
            if isinstance(pattern, str) and pattern.strip():
                self._path_rules.append(PathRule(pattern=pattern.strip(), allow=allow))
            else:
                logger.warning(
                    "Skipping path rule with missing, empty, or non-string 'pattern' field: %r",
                    rule,
                )

    def remember_allow(self, decision: PermissionDecision) -> None:
        """Remember a user-approved allow pattern for the current process."""
        permission = decision.permission.strip()
        patterns = decision.always_patterns or decision.patterns
        if not permission or not patterns:
            return
        existing = {(rule.permission, rule.pattern) for rule in self._remembered_allow_rules}
        for pattern in patterns:
            normalized = pattern.strip()
            if not normalized or (permission, normalized) in existing:
                continue
            self._remembered_allow_rules.append(
                RememberedAllowRule(permission=permission, pattern=normalized)
            )
            existing.add((permission, normalized))

    def evaluate(
        self,
        tool_name: str,
        *,
        is_read_only: bool,
        file_path: str | None = None,
        command: str | None = None,
    ) -> PermissionDecision:
        """Return whether the tool may run immediately."""
        permission = _permission_name(
            tool_name, is_read_only=is_read_only, file_path=file_path, command=command
        )
        patterns = _permission_patterns(
            permission, tool_name=tool_name, file_path=file_path, command=command
        )
        always_patterns = _always_patterns(permission, patterns=patterns, command=command)

        # Built-in sensitive path protection — always active, cannot be
        # overridden by user settings or permission mode.  This is a
        # defence-in-depth measure against LLM-directed or prompt-injection
        # driven access to credential files.
        if file_path:
            for candidate_path in _policy_match_paths(file_path):
                for pattern in SENSITIVE_PATH_PATTERNS:
                    if fnmatch.fnmatch(candidate_path, pattern):
                        return PermissionDecision(
                            allowed=False,
                            reason=(
                                f"Access denied: {file_path} is a sensitive credential path "
                                f"(matched built-in pattern '{pattern}')"
                            ),
                            permission=permission,
                            patterns=patterns,
                            always_patterns=always_patterns,
                        )

        # Explicit tool deny list
        if tool_name in self._settings.denied_tools:
            return PermissionDecision(
                allowed=False,
                reason=f"{tool_name} is explicitly denied",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Explicit tool allow list
        if tool_name in self._settings.allowed_tools:
            return PermissionDecision(
                allowed=True,
                reason=f"{tool_name} is explicitly allowed",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Check path-level rules
        matched_allow_rule: str | None = None
        if file_path and self._path_rules:
            for candidate_path in _policy_match_paths(file_path):
                for rule in self._path_rules:
                    if fnmatch.fnmatch(candidate_path, rule.pattern):
                        if not rule.allow:
                            return PermissionDecision(
                                allowed=False,
                                reason=f"Path {file_path} matches deny rule: {rule.pattern}",
                                permission=permission,
                                patterns=patterns,
                                always_patterns=always_patterns,
                            )
                        matched_allow_rule = rule.pattern

        # Check command deny patterns (e.g. deny "rm -rf /")
        if command:
            for pattern in getattr(self._settings, "denied_commands", []):
                if isinstance(pattern, str) and fnmatch.fnmatch(command, pattern):
                    return PermissionDecision(
                        allowed=False,
                        reason=f"Command matches deny pattern: {pattern}",
                        permission=permission,
                        patterns=patterns,
                        always_patterns=always_patterns,
                    )

        if matched_allow_rule and is_read_only:
            return PermissionDecision(
                allowed=True,
                reason=f"Path {file_path} matches allow rule: {matched_allow_rule}",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        if self._is_remembered_allowed(permission, patterns):
            return PermissionDecision(
                allowed=True,
                reason=f"{permission} matches a remembered allow rule for this session",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Full auto: allow everything
        if self._settings.mode == PermissionMode.FULL_AUTO:
            return PermissionDecision(
                allowed=True,
                reason="Auto mode allows all tools",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Read-only tools always allowed
        if is_read_only:
            return PermissionDecision(
                allowed=True,
                reason="read-only tools are allowed",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Plan mode: block mutating tools
        if self._settings.mode == PermissionMode.PLAN:
            return PermissionDecision(
                allowed=False,
                reason="Plan mode blocks mutating tools until the user exits plan mode",
                permission=permission,
                patterns=patterns,
                always_patterns=always_patterns,
            )

        # Default mode: require confirmation for mutating tools
        bash_hint = _bash_permission_hint(command)
        reason = (
            "Mutating tools require user confirmation in default mode. "
            "Approve the prompt when asked, or run /permissions full_auto "
            "if you want to allow them for this session."
        )
        if bash_hint:
            reason = f"{reason} {bash_hint}"
        scope_hint = _always_scope_hint(always_patterns)
        if scope_hint:
            reason = f"{reason} {scope_hint}"
        return PermissionDecision(
            allowed=False,
            requires_confirmation=True,
            reason=reason,
            permission=permission,
            patterns=patterns,
            always_patterns=always_patterns,
        )

    def _is_remembered_allowed(self, permission: str, patterns: tuple[str, ...]) -> bool:
        if not patterns:
            return False
        rules = [
            rule
            for rule in self._remembered_allow_rules
            if fnmatch.fnmatch(permission, rule.permission)
        ]
        if not rules:
            return False
        return all(
            any(fnmatch.fnmatch(pattern, rule.pattern) for rule in rules) for pattern in patterns
        )


def _policy_match_paths(file_path: str) -> tuple[str, ...]:
    """Return path forms that should participate in policy matching.

    Directory-scoped tools like ``grep`` and ``glob`` may operate on a root such
    as ``/home/user/.ssh``. Appending a trailing slash lets glob-style deny
    patterns like ``*/.ssh/*`` and ``/etc/*`` match the directory root itself.
    """
    normalized = file_path.rstrip("/")
    if not normalized:
        return (file_path,)
    return (normalized, normalized + "/")


def _permission_name(
    tool_name: str,
    *,
    is_read_only: bool,
    file_path: str | None,
    command: str | None,
) -> str:
    if command or tool_name == "bash":
        return "bash"
    if file_path and not is_read_only:
        return "edit"
    return tool_name


def _permission_patterns(
    permission: str,
    *,
    tool_name: str,
    file_path: str | None,
    command: str | None,
) -> tuple[str, ...]:
    if permission == "bash" and command:
        return (command,)
    if file_path:
        return (file_path,)
    return (tool_name,)


def _always_patterns(
    permission: str,
    *,
    patterns: tuple[str, ...],
    command: str | None,
) -> tuple[str, ...]:
    if permission == "bash" and command:
        return (_bash_command_allow_pattern(command),)
    return patterns


def _always_scope_hint(always_patterns: tuple[str, ...]) -> str:
    if not always_patterns:
        return ""
    formatted = ", ".join(always_patterns[:3])
    if len(always_patterns) > 3:
        formatted = f"{formatted}, ..."
    return f"Choosing Always will allow this session pattern: {formatted}."


def _bash_command_allow_pattern(command: str) -> str:
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    if not tokens:
        return command

    if tokens[0] in SAFE_SINGLE_WORD_COMMANDS:
        return f"{tokens[0]} *"

    if len(tokens) >= 2:
        prefix = f"{tokens[0]} {tokens[1]}"
        if prefix in SAFE_BASH_ALWAYS_PREFIXES:
            return f"{prefix} *"

    return command


def _bash_permission_hint(command: str | None) -> str:
    if not command:
        return ""
    lowered = command.lower()
    if any(marker in lowered for marker in INSTALL_MARKERS):
        return (
            "Package installation and scaffolding commands change the workspace, "
            "so they will not run automatically in default mode."
        )
    return ""
