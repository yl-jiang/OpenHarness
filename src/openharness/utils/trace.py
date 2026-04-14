"""Lightweight structured runtime trace logging for debugging session flow."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from openharness.config.paths import get_logs_dir

__all__ = ["get_trace_path", "trace_event"]

_TRACE_LOCK = threading.Lock()
_FALSEY_ENV_VALUES = {"", "0", "false", "no", "off"}


def get_trace_path(*, session_id: str | None = None) -> Path | None:
    """Return the active trace file path, if runtime tracing is enabled."""
    explicit = os.environ.get("OPENHARNESS_TRACE_FILE")
    if explicit:
        return Path(explicit).expanduser()

    enabled = os.environ.get("OPENHARNESS_TRACE", "")
    if enabled.strip().lower() in _FALSEY_ENV_VALUES:
        return None

    if not enabled:
        return None

    suffix = session_id or str(os.getpid())
    return get_logs_dir() / f"runtime-trace-{suffix}.jsonl"


def trace_event(
    event: str,
    *,
    component: str,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    """Append one JSON-lines trace record to the active trace file."""
    path = get_trace_path(session_id=session_id)
    if path is None:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.time(),
        "event": event,
        "component": component,
    }
    if session_id:
        record["session_id"] = session_id
    record.update(fields)

    payload = json.dumps(record, ensure_ascii=True, sort_keys=True, default=str)
    with _TRACE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(payload)
            handle.write("\n")
