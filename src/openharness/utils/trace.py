"""Backward-compatible trace helpers built on the unified logger."""

from __future__ import annotations

from typing import Any

from openharness.utils.log import get_trace_path, log_event

__all__ = ["get_trace_path", "trace_event"]


def trace_event(
    event: str,
    *,
    component: str,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    log_event(
        event,
        component=component,
        session_id=session_id,
        **fields,
    )
