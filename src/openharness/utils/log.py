"""Unified logging for OpenHarness, backed by loguru."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from pathlib import Path
from typing import IO, Any

from loguru import logger as _loguru_logger

from openharness.config.paths import get_logs_dir

__all__ = [
    "configure_logging",
    "get_default_log_path",
    "get_logger",
    "get_trace_path",
    "log_event",
    "reset_logging",
]

_FALSEY_ENV_VALUES = {"", "0", "false", "no", "off"}
_LEVELS_BY_NUMBER = {
    10: "DEBUG",
    20: "INFO",
    30: "WARNING",
    40: "ERROR",
    50: "CRITICAL",
}
_STDLIB_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}
_CONFIG_LOCK = threading.Lock()
_FILE_LOCK = threading.Lock()
_LOGGER_NAME = "openharness"
_SINK_IDS: list[int] = []
_CURRENT_CONFIG: tuple[str, str, str] | None = None


def _is_truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() not in _FALSEY_ENV_VALUES


def _normalize_component(name: str | None) -> str:
    if not name:
        return "app"
    normalized = name.strip()
    if normalized.startswith(f"{_LOGGER_NAME}."):
        normalized = normalized.removeprefix(f"{_LOGGER_NAME}.")
    return normalized.rsplit(".", 1)[-1]


def _serialize_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _serialize_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_serialize_value(item) for item in value]
    return str(value)


def _sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None:
            continue
        sanitized[str(key)] = _serialize_value(value)
    return sanitized


def _format_fields(fields: dict[str, Any]) -> str:
    if not fields:
        return ""
    return " | " + " ".join(f"{key}={fields[key]}" for key in sorted(fields))


def _format_exception(record: dict[str, Any]) -> str:
    exception = record.get("exception")
    if exception is None:
        return ""
    return "".join(traceback.format_exception(exception.type, exception.value, exception.traceback)).rstrip()


def _write_json_line(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, default=str)
    with _FILE_LOCK:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.write("\n")


def _payload_from_record(record: dict[str, Any]) -> dict[str, Any]:
    fields = _sanitize_fields(dict(record["extra"].get("fields", {})))
    payload: dict[str, Any] = {
        "ts": record["time"].timestamp(),
        "level": record["level"].name,
        "logger": record["extra"].get("logger_name", _LOGGER_NAME),
        "component": record["extra"].get("component", _normalize_component(record["name"])),
        "message": record["message"],
    }
    payload.update(fields)
    exception = _format_exception(record)
    if exception:
        payload["exception"] = exception
    return payload


def _trace_path_for_record(record: dict[str, Any]) -> Path | None:
    fields = record["extra"].get("fields", {})
    if "event" not in fields:
        return None
    session_id = fields.get("session_id")
    return get_trace_path(session_id=session_id if isinstance(session_id, str) else None)


def _build_console_sink(stream: IO[str]):
    def _sink(message) -> None:
        record = message.record
        fields = _sanitize_fields(dict(record["extra"].get("fields", {})))
        component = record["extra"].get("component", _normalize_component(record["name"]))
        rendered = (
            f"{record['time'].strftime('%H:%M:%S')} "
            f"{record['level'].name:<8} "
            f"{component:<18} "
            f"{record['message']}"
            f"{_format_fields(fields)}"
        )
        exception = _format_exception(record)
        if exception:
            rendered = f"{rendered}\n{exception}"
        stream.write(rendered + "\n")
        flush = getattr(stream, "flush", None)
        if callable(flush):
            flush()

    return _sink


def _build_jsonl_sink(path_resolver):
    def _sink(message) -> None:
        record = message.record
        path = path_resolver(record)
        if path is None:
            return
        _write_json_line(path, _payload_from_record(record))

    return _sink


def _resolve_level_name(*, debug: bool = False, level: str | int | None = None) -> str:
    if isinstance(level, int):
        return _LEVELS_BY_NUMBER.get(level, "INFO")
    if isinstance(level, str) and level.strip():
        return level.strip().upper()
    if debug:
        return "DEBUG"
    env_level = os.environ.get("OPENHARNESS_LOG_LEVEL")
    if env_level:
        return env_level.strip().upper()
    return "WARNING"


def _remove_sinks_locked() -> None:
    _loguru_logger.remove()
    _SINK_IDS.clear()
    global _CURRENT_CONFIG
    _CURRENT_CONFIG = None


def get_default_log_path() -> Path:
    explicit = os.environ.get("OPENHARNESS_LOG_FILE")
    if explicit:
        return Path(explicit).expanduser()
    return get_logs_dir() / "openharness.jsonl"


def get_trace_path(*, session_id: str | None = None) -> Path | None:
    explicit = os.environ.get("OPENHARNESS_TRACE_FILE")
    if explicit:
        return Path(explicit).expanduser()

    enabled = os.environ.get("OPENHARNESS_TRACE")
    if not _is_truthy(enabled):
        return None

    suffix = session_id or str(os.getpid())
    return get_logs_dir() / f"runtime-trace-{suffix}.jsonl"


def configure_logging(
    *,
    debug: bool = False,
    level: str | int | None = None,
    console_stream: IO[str] | None = None,
    log_file: str | Path | None = None,
    reset: bool = False,
) -> None:
    global _CURRENT_CONFIG
    level_name = _resolve_level_name(debug=debug, level=level)
    stream = console_stream or sys.stderr
    app_log_path = Path(log_file).expanduser() if log_file is not None else get_default_log_path()
    desired_config = (level_name, str(app_log_path), str(id(stream)))

    with _CONFIG_LOCK:
        if reset:
            _remove_sinks_locked()
        elif _CURRENT_CONFIG == desired_config:
            return
        elif _CURRENT_CONFIG is not None:
            _remove_sinks_locked()

        _SINK_IDS.append(_loguru_logger.add(_build_console_sink(stream), level=level_name, colorize=False))
        _SINK_IDS.append(_loguru_logger.add(_build_jsonl_sink(lambda _record: app_log_path), level="DEBUG"))
        _SINK_IDS.append(_loguru_logger.add(_build_jsonl_sink(_trace_path_for_record), level="DEBUG"))

        _CURRENT_CONFIG = desired_config


def reset_logging() -> None:
    with _CONFIG_LOCK:
        _remove_sinks_locked()


def _format_message(message: str, args: tuple[Any, ...]) -> str:
    if not args:
        return message
    try:
        return message % args
    except Exception:
        try:
            return message.format(*args)
        except Exception:
            return " ".join([message, *[str(arg) for arg in args]])


def _normalize_level(level: str | int) -> str:
    if isinstance(level, int):
        return _LEVELS_BY_NUMBER.get(level, "INFO")
    return level.upper()


def _normalize_exc_info(exc_info: Any) -> bool | tuple[type[BaseException], BaseException, Any] | None:
    if not exc_info:
        return None
    if exc_info is True:
        return True
    if isinstance(exc_info, tuple):
        return exc_info
    if isinstance(exc_info, BaseException):
        return (type(exc_info), exc_info, exc_info.__traceback__)
    return True


class OpenHarnessLogger:
    def __init__(
        self,
        *,
        name: str,
        component: str,
        bound_fields: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._component = component
        self._bound_fields = bound_fields or {}

    def bind(self, **fields: Any) -> "OpenHarnessLogger":
        merged = dict(self._bound_fields)
        merged.update(_sanitize_fields(fields))
        return OpenHarnessLogger(name=self._name, component=self._component, bound_fields=merged)

    def log(self, level: str | int, message: str, *args: Any, **kwargs: Any) -> None:
        exc_info = kwargs.pop("exc_info", None)
        kwargs.pop("stack_info", None)
        kwargs.pop("stacklevel", None)
        extra_fields = kwargs.pop("extra", None)

        fields = dict(self._bound_fields)
        fields.update(_sanitize_fields(kwargs))
        if isinstance(extra_fields, dict):
            fields.update(_sanitize_fields(extra_fields))

        level_name = _normalize_level(level)
        formatted_message = _format_message(message, args)
        normalized_exc_info = _normalize_exc_info(exc_info)

        bound_logger = _loguru_logger.bind(
            logger_name=self._name,
            component=self._component,
            fields=fields,
        )
        if exc_info:
            if isinstance(exc_info, BaseException):
                bound_logger = bound_logger.opt(exception=exc_info)
            elif isinstance(exc_info, tuple):
                bound_logger = bound_logger.opt(exception=exc_info)
            else:
                bound_logger = bound_logger.opt(exception=True)
        bound_logger.log(level_name, formatted_message)

        logging.getLogger(self._name).log(
            _STDLIB_LEVELS[level_name],
            formatted_message,
            exc_info=normalized_exc_info,
            extra={"oh_component": self._component, "oh_fields": fields},
        )

    def debug(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.log("DEBUG", message, *args, **kwargs)

    def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.log("INFO", message, *args, **kwargs)

    def warning(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.log("WARNING", message, *args, **kwargs)

    def error(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.log("ERROR", message, *args, **kwargs)

    def exception(self, message: str, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("exc_info", True)
        self.log("ERROR", message, *args, **kwargs)

    def critical(self, message: str, *args: Any, **kwargs: Any) -> None:
        self.log("CRITICAL", message, *args, **kwargs)

    def event(self, event: str, *, level: str | int = "INFO", message: str | None = None, **fields: Any) -> None:
        payload = {"event": event}
        payload.update(fields)
        self.log(level, message or event, **payload)


def get_logger(name: str | None = None) -> OpenHarnessLogger:
    if _CURRENT_CONFIG is None:
        configure_logging()
    logger_name = name or _LOGGER_NAME
    if logger_name != _LOGGER_NAME and "." not in logger_name:
        logger_name = f"{_LOGGER_NAME}.{logger_name}"
    return OpenHarnessLogger(
        name=logger_name,
        component=_normalize_component(logger_name),
    )


def log_event(
    event: str,
    *,
    component: str,
    session_id: str | None = None,
    level: str | int = "INFO",
    message: str | None = None,
    **fields: Any,
) -> None:
    if session_id:
        fields["session_id"] = session_id
    get_logger(component).event(event, level=level, message=message, **fields)
