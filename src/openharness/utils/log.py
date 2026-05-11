"""Unified logging for OpenHarness, backed by loguru."""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
import sys
import threading
import traceback
from pathlib import Path
from typing import IO, Any

from loguru import logger as _loguru_logger

from openharness.config.paths import get_logs_dir

__all__ = [
    "_DISABLE_CONSOLE",
    "configure_logging",
    "get_default_log_path",
    "get_logger",
    "log_event",
    "reset_logging",
]

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
_CURRENT_CONFIG: tuple[str, str, str, str, str] | None = None
_DEFAULT_ROTATION = "10 MB"
_DEFAULT_RETENTION = "30 days"
_DEFAULT_LOG_BASENAME = "openharness"
_DEFAULT_LOG_SUFFIX = ".jsonl"
_DEFAULT_LOG_RETENTION_GLOB = f"{_DEFAULT_LOG_BASENAME}*{_DEFAULT_LOG_SUFFIX}"
_DEFAULT_LOG_PATH: Path | None = None
_SIZE_PATTERN = re.compile(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB|TB)", re.IGNORECASE)


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


def _parse_size(size_str: str) -> int:
    match = _SIZE_PATTERN.search(size_str)
    if not match:
        return 10 * 1024 * 1024
    value = float(match.group(1))
    unit = match.group(2).upper()
    multipliers = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3, "TB": 1024**4}
    return int(value * multipliers.get(unit, 1))


def _parse_retention_days(retention_str: str) -> int:
    retention_str = retention_str.strip().lower()
    match = re.match(r"^(\d+)\s*(day|days)?$", retention_str)
    if not match:
        return 30
    return int(match.group(1))


def _do_rotate(path: Path) -> None:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup = path.with_name(f"{path.stem}.{timestamp}{path.suffix}")
    counter = 0
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{path.stem}.{timestamp}_{counter}{path.suffix}")
    path.rename(backup)


def _build_timestamped_log_path(
    directory: Path,
    *,
    basename: str = _DEFAULT_LOG_BASENAME,
    suffix: str = _DEFAULT_LOG_SUFFIX,
) -> Path:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    candidate = directory / f"{basename}.{timestamp}{suffix}"
    counter = 0
    while candidate.exists():
        counter += 1
        candidate = directory / f"{basename}.{timestamp}_{counter}{suffix}"
    return candidate


def _cleanup_old_files(path: Path, retention: str, *, cleanup_glob: str | None = None) -> None:
    days = _parse_retention_days(retention)
    if days <= 0:
        return
    cutoff = datetime.datetime.now() - datetime.timedelta(days=days)
    pattern = cleanup_glob or f"{path.stem}.*{path.suffix}"
    for f in path.parent.glob(pattern):
        if f == path:
            continue
        try:
            if datetime.datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except Exception:
            pass


def _maybe_rotate(path: Path, *, rotation: str, retention: str, cleanup_glob: str | None = None) -> None:
    if path.exists():
        if _SIZE_PATTERN.search(rotation):
            size_limit = _parse_size(rotation)
            if path.stat().st_size >= size_limit:
                _do_rotate(path)
        else:
            mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
            now = datetime.datetime.now()
            if mtime.date() != now.date():
                _do_rotate(path)

    if retention:
        _cleanup_old_files(path, retention, cleanup_glob=cleanup_glob)


def _write_json_line(
    path: Path,
    payload: dict[str, Any],
    *,
    rotation: str = _DEFAULT_ROTATION,
    retention: str = _DEFAULT_RETENTION,
    cleanup_glob: str | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    with _FILE_LOCK:
        _maybe_rotate(path, rotation=rotation, retention=retention, cleanup_glob=cleanup_glob)
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


def _build_jsonl_sink(
    path_resolver,
    *,
    rotation: str = _DEFAULT_ROTATION,
    retention: str = _DEFAULT_RETENTION,
    cleanup_glob: str | None = None,
):
    def _sink(message) -> None:
        record = message.record
        path = path_resolver(record)
        if path is None:
            return
        _write_json_line(
            path,
            _payload_from_record(record),
            rotation=rotation,
            retention=retention,
            cleanup_glob=cleanup_glob,
        )

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
    global _DEFAULT_LOG_PATH
    if _DEFAULT_LOG_PATH is None:
        _DEFAULT_LOG_PATH = _build_timestamped_log_path(get_logs_dir())
    return _DEFAULT_LOG_PATH


# Sentinel to indicate "no console output" (distinct from None which means use default)
_DISABLE_CONSOLE = object()


def configure_logging(
    *,
    debug: bool = False,
    level: str | int | None = None,
    console_stream: IO[str] | None | object = None,
    log_file: str | Path | None = None,
    reset: bool = False,
    rotation: str | None = None,
    retention: str | None = None,
) -> None:
    global _CURRENT_CONFIG
    level_name = _resolve_level_name(debug=debug, level=level)
    disable_console = console_stream is _DISABLE_CONSOLE
    stream = None if disable_console else (console_stream or sys.stderr)
    explicit_env_log_file = os.environ.get("OPENHARNESS_LOG_FILE")
    using_generated_default_log = log_file is None and not explicit_env_log_file
    if log_file is not None:
        app_log_path = Path(log_file).expanduser()
    elif explicit_env_log_file:
        app_log_path = Path(explicit_env_log_file).expanduser()
    else:
        app_log_path = get_default_log_path()
    rotation = rotation or os.environ.get("OPENHARNESS_LOG_ROTATION") or _DEFAULT_ROTATION
    retention = retention or os.environ.get("OPENHARNESS_LOG_RETENTION") or _DEFAULT_RETENTION
    cleanup_glob = _DEFAULT_LOG_RETENTION_GLOB if using_generated_default_log else None
    stream_id = "disabled" if disable_console else str(id(stream))
    desired_config = (level_name, str(app_log_path), stream_id, rotation, retention)

    with _CONFIG_LOCK:
        if not reset and _CURRENT_CONFIG == desired_config:
            return

        # Clear any pre-existing loguru sinks before registering OpenHarness-managed
        # sinks so the library's default stderr sink cannot leak logs into TUI mode.
        _remove_sinks_locked()

        # Keep explicit/fixed log files split by process start, but let generated
        # timestamped default files stand on their own without startup rotation.
        if app_log_path.exists() and not using_generated_default_log:
            _do_rotate(app_log_path)

        if stream is not None:
            _SINK_IDS.append(_loguru_logger.add(_build_console_sink(stream), level=level_name, colorize=False))
        _SINK_IDS.append(
            _loguru_logger.add(
                _build_jsonl_sink(
                    lambda _record: app_log_path,
                    rotation=rotation,
                    retention=retention,
                    cleanup_glob=cleanup_glob,
                ),
                level="DEBUG",
            )
        )

        _CURRENT_CONFIG = desired_config


def reset_logging() -> None:
    global _DEFAULT_LOG_PATH
    with _CONFIG_LOCK:
        _remove_sinks_locked()
        _DEFAULT_LOG_PATH = None


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
