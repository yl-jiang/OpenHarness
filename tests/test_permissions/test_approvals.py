"""Tests for ApprovalState, ApprovalCoordinator, and related helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from openharness.permissions.approvals import (
    ApprovalCoordinator,
    ApprovalRequest,
    ApprovalRule,
    ApprovalState,
    _normalize_reply,
)
from openharness.permissions.checker import PermissionChecker
from openharness.config.settings import PermissionSettings
from openharness.permissions.modes import PermissionMode


# ---------------------------------------------------------------------------
# _normalize_reply
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("once", "once"),
        ("always", "always"),
        ("reject", "reject"),
        ("ONCE", "once"),
        ("ALWAYS", "always"),
        ("REJECT", "reject"),
        (True, "once"),
        (False, "reject"),
        (None, "reject"),
        ("other", "once"),  # unrecognized truthy str → "once" (backward compat)
    ],
)
def test_normalize_reply(raw, expected):
    assert _normalize_reply(raw) == expected


# ---------------------------------------------------------------------------
# ApprovalState
# ---------------------------------------------------------------------------


def test_approval_state_match_tool_no_rules():
    state = ApprovalState()
    assert state.match(ApprovalRule(kind="tool", permission="edit", pattern="demo.py")) is False


def test_approval_state_remember_and_match_tool():
    state = ApprovalState()
    state.remember(ApprovalRule(kind="tool", permission="edit", pattern="/tmp/demo.py"))
    assert state.match(ApprovalRule(kind="tool", permission="edit", pattern="/tmp/demo.py")) is True
    assert state.match(ApprovalRule(kind="tool", permission="edit", pattern="/tmp/other.py")) is False


def test_approval_state_remember_bash_always_pattern():
    state = ApprovalState()
    state.remember(ApprovalRule(kind="tool", permission="bash", pattern="git remote *"))
    assert state.match(ApprovalRule(kind="tool", permission="bash", pattern="git remote show origin")) is True
    assert state.match(ApprovalRule(kind="tool", permission="bash", pattern="git push origin main")) is False


def test_approval_state_duplicate_rule_not_added():
    state = ApprovalState()
    rule = ApprovalRule(kind="tool", permission="edit", pattern="/tmp/demo.py")
    state.remember(rule)
    state.remember(rule)
    assert len(state._rules) == 1


# ---------------------------------------------------------------------------
# ApprovalCoordinator — authorize_tool
# ---------------------------------------------------------------------------


def _make_checker(mode: PermissionMode = PermissionMode.DEFAULT) -> PermissionChecker:
    return PermissionChecker(PermissionSettings(mode=mode))


@pytest.mark.asyncio
async def test_coordinator_allows_read_only_tool_without_prompt():
    checker = _make_checker()
    prompt = AsyncMock(return_value="reject")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    decision = await coordinator.authorize_tool("read_file", is_read_only=True)

    assert decision.allowed is True
    prompt.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_calls_prompt_for_mutating_tool():
    checker = _make_checker()
    prompt = AsyncMock(return_value="once")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    decision = await coordinator.authorize_tool("bash", is_read_only=False, command="rm -rf /tmp/x")

    assert decision.allowed is True
    prompt.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_remembers_always_tool_approval():
    checker = _make_checker()
    prompt = AsyncMock(return_value="always")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    first = await coordinator.authorize_tool("bash", is_read_only=False, command="git remote -v")
    assert first.allowed is True

    second = await coordinator.authorize_tool("bash", is_read_only=False, command="git remote show origin")
    assert second.allowed is True
    # Second call must be served from ApprovalState without prompting again.
    assert prompt.call_count == 1


@pytest.mark.asyncio
async def test_coordinator_reject_blocks_tool():
    checker = _make_checker()
    prompt = AsyncMock(return_value="reject")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    decision = await coordinator.authorize_tool("bash", is_read_only=False, command="rm /tmp/x")

    assert decision.allowed is False
    prompt.assert_called_once()


@pytest.mark.asyncio
async def test_coordinator_prompts_edit_file_with_diff_preview():
    """edit_file goes through the same authorize_tool path; diff is included in request."""
    checker = _make_checker()
    received: list[ApprovalRequest] = []

    async def capture(request: ApprovalRequest) -> str:
        received.append(request)
        return "once"

    coordinator = ApprovalCoordinator(checker, prompt_fn=capture)

    decision = await coordinator.authorize_tool(
        "edit_file",
        is_read_only=False,
        file_path="/tmp/x.py",
        preview=("--- /tmp/x.py\n+++ /tmp/x.py\n@@ -1 +1 @@\n-old\n+new\n", 1, 1),
    )

    assert decision.allowed is True
    assert len(received) == 1
    assert received[0].diff != ""


@pytest.mark.asyncio
async def test_coordinator_edit_file_always_remembered_per_file():
    """'always' for edit_file is stored per file path, not globally."""
    checker = _make_checker()
    prompt = AsyncMock(return_value="always")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    preview = ("--- /tmp/a.py\n+new\n", 1, 0)
    await coordinator.authorize_tool("edit_file", is_read_only=False, file_path="/tmp/a.py", preview=preview)
    # Same file again — must not prompt
    await coordinator.authorize_tool("edit_file", is_read_only=False, file_path="/tmp/a.py", preview=preview)
    assert prompt.call_count == 1

    # Different file — must prompt
    await coordinator.authorize_tool("edit_file", is_read_only=False, file_path="/tmp/b.py", preview=preview)
    assert prompt.call_count == 2


@pytest.mark.asyncio
async def test_coordinator_full_auto_allows_all_tools():
    checker = _make_checker(PermissionMode.FULL_AUTO)
    prompt = AsyncMock(return_value="reject")
    coordinator = ApprovalCoordinator(checker, prompt_fn=prompt)

    decision = await coordinator.authorize_tool("bash", is_read_only=False, command="rm -rf /")

    assert decision.allowed is True
    prompt.assert_not_called()


# ---------------------------------------------------------------------------
# ApprovalCoordinator — set_checker preserves state
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_set_checker_preserves_approval_state():
    checker1 = _make_checker()
    always_prompt = AsyncMock(return_value="always")
    coordinator = ApprovalCoordinator(checker1, prompt_fn=always_prompt)

    # Approve once; state remembers it.
    await coordinator.authorize_tool("bash", is_read_only=False, command="git remote -v")

    # Replace checker — state must survive.
    checker2 = _make_checker(PermissionMode.DEFAULT)
    coordinator.set_checker(checker2)

    second = await coordinator.authorize_tool("bash", is_read_only=False, command="git remote show origin")
    assert second.allowed is True
    # Still only one prompt call.
    assert always_prompt.call_count == 1


# ---------------------------------------------------------------------------
# Notify function fires before prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_notify_fn_fires_before_prompt():
    checker = _make_checker()
    notify_calls: list[ApprovalRequest] = []

    async def notify(request: ApprovalRequest) -> None:
        notify_calls.append(request)

    prompt = AsyncMock(return_value="once")
    coordinator = ApprovalCoordinator(
        checker, prompt_fn=prompt, notify_fn=notify
    )

    await coordinator.authorize_tool("bash", is_read_only=False, command="rm /tmp/x")

    assert len(notify_calls) == 1
    assert notify_calls[0].kind == "tool"
    assert notify_calls[0].tool_name == "bash"
