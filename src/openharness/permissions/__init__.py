"""Permission helpers for OpenHarness."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from openharness.permissions.approvals import (
        ApprovalCoordinator,
        ApprovalReply,
        ApprovalRequest,
        ApprovalRule,
        ApprovalState,
    )
    from openharness.permissions.checker import PermissionChecker, PermissionDecision
    from openharness.permissions.modes import PermissionMode

__all__ = [
    "ApprovalCoordinator",
    "ApprovalReply",
    "ApprovalRequest",
    "ApprovalRule",
    "ApprovalState",
    "PermissionChecker",
    "PermissionDecision",
    "PermissionMode",
]


def __getattr__(name: str):
    if name in {"PermissionChecker", "PermissionDecision"}:
        from openharness.permissions.checker import PermissionChecker, PermissionDecision

        return {
            "PermissionChecker": PermissionChecker,
            "PermissionDecision": PermissionDecision,
        }[name]
    if name == "PermissionMode":
        from openharness.permissions.modes import PermissionMode

        return PermissionMode
    if name in {"ApprovalCoordinator", "ApprovalReply", "ApprovalRequest", "ApprovalRule", "ApprovalState"}:
        from openharness.permissions import approvals as _approvals

        return getattr(_approvals, name)
    raise AttributeError(name)
