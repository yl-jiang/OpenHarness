"""Parser and formatter for the ``/goal`` slash command.

The handler lives in ``commands/registry.py`` (inline, like all other built-in
commands). This module only holds pure helpers so it can be imported by tests
without pulling in the full registry.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from openharness.goal.state import MAX_GOAL_OBJECTIVE_LENGTH, GoalSnapshot

CONTROL_SUBCOMMANDS = frozenset({"pause", "resume", "cancel", "next", "skip"})
QUEUE_SUBCOMMANDS = frozenset({"add", "remove", "clear", "reorder"})


class ParsedGoalCommand(TypedDict, total=False):
    kind: Literal[
        "status",
        "pause",
        "resume",
        "cancel",
        "next",
        "skip",
        "create",
        "queue_list",
        "queue_add",
        "queue_remove",
        "queue_clear",
        "error",
    ]
    objective: str
    replace: bool
    message: str
    queue_id: str
    priority: int


def parse_goal_command(args: str) -> ParsedGoalCommand:
    """Parse ``/goal <args>`` into a typed result.

    Mirrors the kimi-code ``parseGoalCommand`` grammar: reserved words are
    only subcommands when they stand alone as the first token; ``replace``
    may precede the objective; ``--`` forces the rest to be the objective.

    Phase 6 extensions: ``/goal queue``, ``/goal next``, ``/goal skip``.
    """
    stripped = args.strip()
    if not stripped or stripped == "status":
        return {"kind": "status"}

    tokens = stripped.split()
    first = tokens[0]

    # Solo control word → subcommand.
    if first in CONTROL_SUBCOMMANDS and len(tokens) == 1:
        return {"kind": first}  # type: ignore[typeddict-item]

    # Queue subcommand family: ``/goal queue [add|remove|clear] ...``.
    if first == "queue":
        return _parse_queue_subcommand(tokens[1:])

    replace = False
    index = 0
    if tokens[index] == "replace":
        replace = True
        index += 1
    # ``--`` separator lets the objective start with a reserved word.
    if index < len(tokens) and tokens[index] == "--":
        index += 1

    objective = " ".join(tokens[index:]).strip()
    if not objective:
        return {
            "kind": "error",
            "message": "Provide a goal objective, e.g. `/goal Ship feature X`.",
        }
    if len(objective) > MAX_GOAL_OBJECTIVE_LENGTH:
        return {
            "kind": "error",
            "message": f"Objective too long (max {MAX_GOAL_OBJECTIVE_LENGTH} chars).",
        }
    return {"kind": "create", "objective": objective, "replace": replace}


def _parse_queue_subcommand(tokens: list[str]) -> ParsedGoalCommand:
    """Parse the tail of ``/goal queue ...``."""
    if not tokens:
        return {"kind": "queue_list"}

    head = tokens[0]
    rest = tokens[1:]

    if head == "clear" and not rest:
        return {"kind": "queue_clear"}

    if head == "remove" and len(rest) == 1:
        return {"kind": "queue_remove", "queue_id": rest[0]}

    if head == "add":
        priority = 0
        objective_tokens = list(rest)
        # Support ``--priority N`` flag.
        if (
            len(objective_tokens) >= 2
            and objective_tokens[0] == "--priority"
        ):
            try:
                priority = int(objective_tokens[1])
            except ValueError:
                return {
                    "kind": "error",
                    "message": "/goal queue add --priority requires an integer.",
                }
            objective_tokens = objective_tokens[2:]
        # Allow ``--`` separator before the objective.
        if objective_tokens and objective_tokens[0] == "--":
            objective_tokens = objective_tokens[1:]
        objective = " ".join(objective_tokens).strip()
        if not objective:
            return {
                "kind": "error",
                "message": "Provide a goal objective, e.g. `/goal queue add Ship feature Y`.",
            }
        if len(objective) > MAX_GOAL_OBJECTIVE_LENGTH:
            return {
                "kind": "error",
                "message": f"Objective too long (max {MAX_GOAL_OBJECTIVE_LENGTH} chars).",
            }
        return {
            "kind": "queue_add",
            "objective": objective,
            "priority": priority,
        }

    return {
        "kind": "error",
        "message": (
            "Usage: /goal queue [add|remove|clear]. "
            "Examples: `/goal queue add Ship feature Y`, "
            "`/goal queue remove <id>`, `/goal queue clear`."
        ),
    }


def format_goal_status(snapshot: GoalSnapshot) -> str:
    """Render a human-readable ``/goal status`` summary."""
    budget = snapshot.budget
    lines = [
        f"Objective: {snapshot.objective}",
    ]
    if snapshot.completion_criterion:
        lines.append(f"Criterion: {snapshot.completion_criterion}")
    lines.append(f"Status: {snapshot.status}")
    if snapshot.terminal_reason:
        lines.append(f"Reason: {snapshot.terminal_reason}")
    lines.append(
        f"Progress: {snapshot.turns_used} turns, "
        f"{snapshot.tokens_used} tokens, "
        f"{snapshot.wall_clock_ms // 1000}s elapsed"
    )

    budget_parts: list[str] = []
    if budget.turn_budget is not None:
        budget_parts.append(f"turns {snapshot.turns_used}/{budget.turn_budget}")
    if budget.token_budget is not None:
        budget_parts.append(f"tokens {snapshot.tokens_used}/{budget.token_budget}")
    if budget.wall_clock_budget_ms is not None:
        budget_parts.append(
            f"time {snapshot.wall_clock_ms // 1000}s/"
            f"{budget.wall_clock_budget_ms // 1000}s"
        )
    if budget_parts:
        lines.append("Budget: " + "; ".join(budget_parts))
    else:
        lines.append("Budget: none set")
    return "\n".join(lines)
