"""Shared constants used by both wolo and solo projects."""

from __future__ import annotations

# --- Time / Calendar ---

WEEKDAYS_ZH: list[str] = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

SEASONS_ZH: list[str] = ["春季", "夏季", "秋季", "冬季"]

TIME_PERIODS_ZH: list[str] = ["凌晨", "清晨", "上午", "中午", "下午", "傍晚", "深夜"]

# --- Report ---

REPORT_TYPE_LABELS: dict[str, str] = {"weekly": "周报", "monthly": "月报", "yearly": "年报"}

REPORT_WINDOW_DAYS: dict[str, int] = {"weekly": 7, "monthly": 30, "yearly": 365}

# --- Default field values ---

DEFAULT_SOURCE_ORIGINAL: str = "原始"
DEFAULT_SOURCE_BACKFILL: str = "补录"
DEFAULT_SAMPLE_TYPE: str = "neutral"
DEFAULT_STATUS: str = "pending"
DEFAULT_PRIORITY: str = "medium"
DEFAULT_PROVIDER_PROFILE: str = "deepseek"
DEFAULT_EMOTION: str = "中性"
DEFAULT_EXPERIMENT_STATUS: str = "active"
DEFAULT_ARTIFACT_SOURCE: str = "derived"

# --- Gateway / Runner ---

GATEWAY_EXCLUDED_CHANNELS: frozenset[str] = frozenset({"cli", "system", "heartbeat"})

HIDDEN_ARGS: frozenset[str] = frozenset({"source_context", "metadata", "session_key"})

PROFILE_UPDATE_ACCEPTED_STATUSES: frozenset[str] = frozenset({"accepted", "pending"})

# --- Auth error patterns (bridge.py) ---

AUTH_ERROR_MESSAGES: list[tuple[str, str]] = [
    ("claude oauth refresh failed", "Claude 订阅认证过期，请重新运行 `oh auth claude-login`。"),
    (
        "claude oauth refresh token is invalid or expired",
        "Claude 订阅 token 已过期，请运行 `claude auth login` 后重新执行 `oh auth claude-login`。",
    ),
]
