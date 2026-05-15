from __future__ import annotations

import math

import pytest

from openharness.swarm.agent_run_context import AgentRunContext, DelegationError


def test_agent_run_context_spawns_leaf_child_by_default():
    root = AgentRunContext.root("root-session")

    updated_parent, child = root.spawn_child(agent_profile="worker")

    assert updated_parent.spawned_children == 1
    assert child.parent_session_id == "root-session"
    assert child.root_session_id == "root-session"
    assert child.lineage_depth == 1
    assert child.agent_profile == "worker"
    assert child.delegation_depth_remaining == 0
    assert child.max_children == 0
    assert child.orchestration_allowed is False


def test_agent_run_context_limits_primary_child_budget_across_session():
    root = AgentRunContext.root("root-session", max_children=1)

    updated_parent, _ = root.spawn_child(agent_profile="worker")

    with pytest.raises(DelegationError, match="child budget"):
        updated_parent.spawn_child(agent_profile="worker-2")


def test_agent_run_context_supports_infinite_child_budget():
    root = AgentRunContext.root("root-session", max_children=math.inf)

    updated_parent, _ = root.spawn_child(agent_profile="worker-1")
    updated_parent, _ = updated_parent.spawn_child(agent_profile="worker-2")

    assert math.isinf(updated_parent.max_children)
    assert updated_parent.spawned_children == 2
