from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

from openharness.utils.log import configure_logging, get_default_log_path, get_logger, reset_logging
from openharness.utils.trace import trace_event


def _read_json_lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_logger_event_writes_structured_records_to_app_and_trace_files(tmp_path, monkeypatch) -> None:
    app_log = tmp_path / "openharness.jsonl"
    trace_log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("OPENHARNESS_LOG_FILE", str(app_log))
    monkeypatch.setenv("OPENHARNESS_TRACE_FILE", str(trace_log))
    reset_logging()
    configure_logging(console_stream=io.StringIO())

    logger = get_logger("openharness.tools.agent_tool")
    logger.event(
        "agent_tool_execute_start",
        session_id="sess-123",
        subagent_type="Explore",
        prompt_length=42,
    )

    app_records = _read_json_lines(app_log)
    trace_records = _read_json_lines(trace_log)

    assert app_records[-1]["event"] == "agent_tool_execute_start"
    assert app_records[-1]["component"] == "agent_tool"
    assert app_records[-1]["session_id"] == "sess-123"
    assert app_records[-1]["subagent_type"] == "Explore"
    assert app_records[-1]["prompt_length"] == 42

    assert trace_records[-1]["event"] == "agent_tool_execute_start"
    assert trace_records[-1]["component"] == "agent_tool"
    assert trace_records[-1]["session_id"] == "sess-123"


def test_logger_file_keeps_utf8_text_readable(tmp_path, monkeypatch) -> None:
    app_log = tmp_path / "openharness.jsonl"
    monkeypatch.setenv("OPENHARNESS_LOG_FILE", str(app_log))
    reset_logging()
    configure_logging(console_stream=io.StringIO())

    logger = get_logger("openharness.ui.runtime")
    logger.info(
        "处理任务完成",
        line="调研一下这个project，先列todo，然后逐条进行",
        status="完成",
        emoji="✅",
    )

    raw = app_log.read_text(encoding="utf-8")
    assert "处理任务完成" in raw
    assert "调研一下这个project，先列todo，然后逐条进行" in raw
    assert '"status": "完成"' in raw
    assert '"emoji": "✅"' in raw
    assert "\\u5904\\u7406" not in raw
    assert "\\u8c03\\u7814" not in raw
    assert "\\u2705" not in raw


def test_logger_console_output_is_pretty_and_contextual(tmp_path, monkeypatch) -> None:
    app_log = tmp_path / "openharness.jsonl"
    console = io.StringIO()
    monkeypatch.setenv("OPENHARNESS_LOG_FILE", str(app_log))
    reset_logging()
    configure_logging(console_stream=console)

    logger = get_logger("openharness.ui.runtime")
    logger.warning(
        "runtime auth failed",
        session_id="worker-session",
        provider="anthropic",
        model="claude-sonnet-4-6",
    )

    output = console.getvalue()
    assert "WARNING" in output
    assert "runtime" in output
    assert "runtime auth failed" in output
    assert "session_id=worker-session" in output
    assert "provider=anthropic" in output
    assert "model=claude-sonnet-4-6" in output


def test_configure_logging_removes_preexisting_console_sink_when_console_disabled(
    tmp_path, monkeypatch
) -> None:
    app_log = tmp_path / "openharness.jsonl"
    repo_root = Path(__file__).resolve().parents[2]
    python_path_parts = [str(repo_root / "src")]
    existing_python_path = os.environ.get("PYTHONPATH")
    if existing_python_path:
        python_path_parts.append(existing_python_path)

    env = os.environ.copy()
    env["OPENHARNESS_LOG_FILE"] = str(app_log)
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from openharness.utils.log import _DISABLE_CONSOLE, configure_logging, get_logger; "
                "configure_logging(console_stream=_DISABLE_CONSOLE); "
                "get_logger('openharness.ui.backend_host').info("
                "'backend log should stay off stderr', event='backend_event')"
            ),
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    app_records = _read_json_lines(app_log)
    assert app_records[-1]["message"] == "backend log should stay off stderr"
    assert app_records[-1]["event"] == "backend_event"


def test_trace_event_uses_unified_logger_backend(tmp_path, monkeypatch) -> None:
    app_log = tmp_path / "openharness.jsonl"
    trace_log = tmp_path / "trace.jsonl"
    monkeypatch.setenv("OPENHARNESS_LOG_FILE", str(app_log))
    monkeypatch.setenv("OPENHARNESS_TRACE_FILE", str(trace_log))
    reset_logging()
    configure_logging(console_stream=io.StringIO())

    trace_event(
        "runtime_auth_resolution_failed",
        component="runtime",
        session_id="sess-auth",
        provider="anthropic",
        error="No credentials found",
    )

    app_records = _read_json_lines(app_log)
    trace_records = _read_json_lines(trace_log)

    assert get_default_log_path() == app_log
    assert app_records[-1]["component"] == "runtime"
    assert app_records[-1]["event"] == "runtime_auth_resolution_failed"
    assert app_records[-1]["error"] == "No credentials found"
    assert trace_records[-1]["event"] == "runtime_auth_resolution_failed"


def test_unified_log_module_uses_loguru_backend() -> None:
    source = (Path(__file__).resolve().parents[2] / "src/openharness/utils/log.py").read_text(encoding="utf-8")
    assert "from loguru import logger as _loguru_logger" in source
