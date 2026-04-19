"""Task data models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal


TaskType = Literal["local_bash", "local_agent", "remote_agent", "in_process_teammate"]


class TaskStatus(str, Enum):
    """Lifecycle states for a background task.

    Inherits from ``str`` so values compare equal to their plain-string
    equivalents (``TaskStatus.RUNNING == "running"`` is ``True``) and
    serialise transparently in JSON and log output.

    Allowed transitions::

        (created) → RUNNING → COMPLETED
                             └→ FAILED
                   └→ KILLED  (via stop_task)
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"

    def __str__(self) -> str:
        return self.value

    @classmethod
    def terminal_states(cls) -> frozenset[TaskStatus]:
        """States from which no further transition is expected."""
        return frozenset({cls.COMPLETED, cls.FAILED, cls.KILLED})


@dataclass
class TaskRecord:
    """Runtime representation of a background task."""

    id: str
    type: TaskType
    status: TaskStatus
    description: str
    cwd: str
    output_file: Path
    command: str | None = None
    prompt: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    ended_at: float | None = None
    return_code: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)
