"""Structured agent-run context and delegation policy for spawned workers."""

from __future__ import annotations

import json
import math
import os
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING, Any, Mapping
from uuid import uuid4

from openharness.engine.types import ToolMetadataKey

if TYPE_CHECKING:
    from openharness.tools.base import ToolRegistry

AGENT_RUN_CONTEXT_ENV_VAR = "OPENHARNESS_AGENT_RUN_CONTEXT"
TOOL_METADATA_UPDATES_KEY = "tool_metadata_updates"
DEFAULT_PRIMARY_DELEGATION_DEPTH = 1
DEFAULT_PRIMARY_MAX_CHILDREN = 16
ORCHESTRATION_TOOL_NAMES = frozenset(
    {
        "agent",
        "task_create",
        "task_get",
        "task_list",
        "task_output",
        "task_stop",
        "task_update",
        "task_wait",
        "send_message",
        "team_create",
        "team_delete",
    }
)


class DelegationError(RuntimeError):
    """Raised when a session is not allowed to spawn more workers."""


def _coerce_non_negative_int(value: object, *, default: int) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, resolved)


def _coerce_non_negative_child_budget(value: object, *, default: int | float) -> int | float:
    if isinstance(value, str) and value.strip().lower() == "infinity":
        return math.inf
    if isinstance(value, bool):
        return default
    if isinstance(value, float):
        if math.isinf(value):
            return math.inf if value > 0 else default
        if not value.is_integer():
            return default
        return max(0, int(value))
    try:
        resolved = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(0, resolved)


def _new_run_id() -> str:
    return uuid4().hex[:12]


@dataclass(frozen=True)
class AgentRunContext:
    """Lifecycle and delegation metadata for one runtime session."""

    session_id: str
    root_session_id: str
    parent_session_id: str | None
    run_id: str
    root_run_id: str
    parent_run_id: str | None
    session_role: str
    agent_profile: str | None = None
    lineage_depth: int = 0
    delegation_depth_remaining: int = 0
    max_children: int | float = 0
    spawned_children: int = 0
    orchestration_allowed: bool = False

    @classmethod
    def root(
        cls,
        session_id: str,
        *,
        delegation_depth: int = DEFAULT_PRIMARY_DELEGATION_DEPTH,
        max_children: int | float = DEFAULT_PRIMARY_MAX_CHILDREN,
    ) -> "AgentRunContext":
        run_id = _new_run_id()
        delegation_depth = max(0, int(delegation_depth))
        max_children = _coerce_non_negative_child_budget(
            max_children,
            default=DEFAULT_PRIMARY_MAX_CHILDREN,
        )
        return cls(
            session_id=session_id,
            root_session_id=session_id,
            parent_session_id=None,
            run_id=run_id,
            root_run_id=run_id,
            parent_run_id=None,
            session_role="primary",
            agent_profile=None,
            lineage_depth=0,
            delegation_depth_remaining=delegation_depth,
            max_children=max_children,
            spawned_children=0,
            orchestration_allowed=delegation_depth > 0 and max_children > 0,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> "AgentRunContext":
        session_id = str(payload.get("session_id") or "")
        run_id = str(payload.get("run_id") or _new_run_id())
        root_run_id = str(payload.get("root_run_id") or run_id)
        root_session_id = str(payload.get("root_session_id") or session_id)
        parent_session = payload.get("parent_session_id")
        parent_run = payload.get("parent_run_id")
        agent_profile = payload.get("agent_profile")
        return cls(
            session_id=session_id,
            root_session_id=root_session_id or session_id,
            parent_session_id=str(parent_session) if parent_session else None,
            run_id=run_id,
            root_run_id=root_run_id,
            parent_run_id=str(parent_run) if parent_run else None,
            session_role=str(payload.get("session_role") or "subagent"),
            agent_profile=str(agent_profile) if agent_profile else None,
            lineage_depth=_coerce_non_negative_int(payload.get("lineage_depth"), default=0),
            delegation_depth_remaining=_coerce_non_negative_int(
                payload.get("delegation_depth_remaining"), default=0
            ),
            max_children=_coerce_non_negative_child_budget(payload.get("max_children"), default=0),
            spawned_children=_coerce_non_negative_int(payload.get("spawned_children"), default=0),
            orchestration_allowed=bool(payload.get("orchestration_allowed")),
        )

    def materialize_for_session(self, session_id: str) -> "AgentRunContext":
        root_session_id = self.root_session_id or session_id
        if self.lineage_depth == 0:
            root_session_id = session_id
        return replace(
            self,
            session_id=session_id,
            root_session_id=root_session_id,
        )

    def to_metadata(self) -> dict[str, object]:
        return asdict(self)

    def to_env_payload(self) -> str:
        return json.dumps(self.to_metadata(), ensure_ascii=True, sort_keys=True)

    def spawn_child(self, *, agent_profile: str | None = None) -> tuple["AgentRunContext", "AgentRunContext"]:
        """Consume one child slot and return the updated parent plus child context."""
        if not self.orchestration_allowed:
            raise DelegationError(
                "Delegation blocked: this session is a leaf worker and cannot spawn more background tasks or agents."
            )
        if self.delegation_depth_remaining <= 0:
            raise DelegationError("Delegation blocked: descendant depth budget is exhausted for this session.")
        if self.spawned_children >= self.max_children:
            raise DelegationError(
                "Delegation blocked: this session already used its child budget "
                f"({self.spawned_children}/{self.max_children})."
            )

        updated_parent = replace(self, spawned_children=self.spawned_children + 1)
        child_depth_remaining = max(0, self.delegation_depth_remaining - 1)
        child_can_delegate = child_depth_remaining > 0 and self.max_children > 0
        child_run_id = _new_run_id()
        child_role = "utility" if child_can_delegate else "subagent"
        child = AgentRunContext(
            session_id="",
            root_session_id=self.root_session_id or self.session_id,
            parent_session_id=self.session_id or None,
            run_id=child_run_id,
            root_run_id=self.root_run_id or self.run_id,
            parent_run_id=self.run_id,
            session_role=child_role,
            agent_profile=agent_profile,
            lineage_depth=self.lineage_depth + 1,
            delegation_depth_remaining=child_depth_remaining,
            max_children=self.max_children if child_can_delegate else 0,
            spawned_children=0,
            orchestration_allowed=child_can_delegate,
        )
        return updated_parent, child


def resolve_agent_run_context(
    metadata: Mapping[str, object] | None,
    *,
    session_id: str | None = None,
) -> AgentRunContext:
    """Return the active agent-run context, defaulting to a primary root session."""
    if isinstance(metadata, Mapping):
        raw = metadata.get(ToolMetadataKey.AGENT_RUN_CONTEXT.value)
        if isinstance(raw, AgentRunContext):
            context = raw
        elif isinstance(raw, Mapping):
            context = AgentRunContext.from_mapping(raw)
        else:
            resolved_session_id = session_id or str(metadata.get("session_id") or "") or "main"
            return AgentRunContext.root(resolved_session_id)
        if session_id:
            return context.materialize_for_session(session_id)
        return context
    return AgentRunContext.root(session_id or "main")


def load_agent_run_context_from_env(*, session_id: str) -> AgentRunContext | None:
    """Parse the child-session run context passed in the worker environment."""
    raw = os.environ.get(AGENT_RUN_CONTEXT_ENV_VAR)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return AgentRunContext.from_mapping(payload).materialize_for_session(session_id)


def apply_agent_run_context(metadata: dict[str, Any] | None, context: AgentRunContext) -> None:
    """Store the current agent-run context on mutable tool metadata."""
    if not isinstance(metadata, dict):
        return
    metadata[ToolMetadataKey.AGENT_RUN_CONTEXT.value] = context.to_metadata()


def build_tool_metadata_updates(context: AgentRunContext) -> dict[str, object]:
    """Return a QueryEngine-friendly metadata patch for the latest run context."""
    return {
        TOOL_METADATA_UPDATES_KEY: {
            ToolMetadataKey.AGENT_RUN_CONTEXT.value: context.to_metadata(),
        }
    }


def apply_orchestration_tool_policy(tool_registry: "ToolRegistry", context: AgentRunContext) -> None:
    """Hide orchestration-plane tools from leaf child sessions."""
    if context.orchestration_allowed:
        return
    for tool_name in ORCHESTRATION_TOOL_NAMES:
        tool_registry.unregister(tool_name)
