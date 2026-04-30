"""Extract local rules from session conversation history."""

from __future__ import annotations

import re
from collections.abc import Iterable

from openharness.utils.log import get_logger

logger = get_logger(__name__)

_LINE_PATTERNS: list[tuple[str, str, re.Pattern[str], float]] = [
    (
        "ssh_host",
        "SSH connection",
        re.compile(r"ssh\s+(?:-[io]\s+\S+\s+)*(\S+@[\d.]+|\S+@\S+)", re.IGNORECASE),
        0.9,
    ),
    (
        "conda_env",
        "Conda environment",
        re.compile(r"conda\s+activate\s+(\S+)"),
        0.9,
    ),
    (
        "api_endpoint",
        "API endpoint",
        re.compile(r"(https?://\S+/v\d+/?)\b"),
        0.9,
    ),
    (
        "env_var",
        "Environment variable",
        re.compile(r"export\s+([A-Z][A-Z0-9_]+)(?:=\S+)?"),
        0.9,
    ),
    (
        "git_remote",
        "Git remote",
        re.compile(r"(?:github|gitlab)\.com[:/](\S+?)(?:\.git)?"),
        0.9,
    ),
    (
        "ray_cluster",
        "Ray cluster",
        re.compile(
            r"ray\s+(?:start|init|submit)\b.*?(--address\s+\S+|\d+\.\d+\.\d+\.\d+:\d+)",
            re.IGNORECASE,
        ),
        0.9,
    ),
]

_DATA_PATH_PATTERN = re.compile(r"(/(?:ext|mnt|home|data|root)\S+)")
_DATA_PATH_CONTEXT_KEYWORDS = {
    "checkpoint",
    "data",
    "dataset",
    "derived",
    "inference",
    "input",
    "landing",
    "load",
    "model_path",
    "mount",
    "output",
    "read",
    "reference",
    "source",
    "target",
    "training",
    "use",
    "write",
}
_DATA_PATH_VALUE_KEYWORDS = (
    "checkpoint",
    "data_manual",
    "dataset",
    "derived",
    "inference",
    "landing",
    "model",
    "reference",
    "training",
)
_DATA_PATH_DENY_PARTS = (
    ".mozilla",
    ".cache",
    ".config",
    "node_modules",
    "/.git",
    ".git/",
    "qdrant",
    "payload_index",
    "segments",
    "/logs/",
    ".log",
    ".bin",
    ".sqlite",
    ".json",
)
_HIGH_VALUE_ENV_VARS = {
    "OPENAI_BASE_URL",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "CUDA_VISIBLE_DEVICES",
    "HF_HOME",
    "HF_TOKEN",
    "MODEL_SCOPE_CACHE",
    "VLLM_USE_MODELSCOPE",
}


def extract_facts_from_text(text: str) -> list[dict]:
    """Extract only high-value environment facts from conversation text."""
    facts: list[dict] = []
    seen_keys: set[str] = set()

    for line in text.splitlines():
        lowered = line.lower()
        for fact_type, label, pattern, confidence in _LINE_PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1) if match.lastindex else match.group(0)
                value = value.strip().rstrip(".,;:)")
                if not value or len(value) < 3:
                    continue
                if fact_type == "env_var" and value not in _HIGH_VALUE_ENV_VARS:
                    continue
                if fact_type == "api_endpoint" and not any(token in lowered for token in ("curl", "endpoint")):
                    continue
                _append_fact(
                    facts,
                    seen_keys,
                    fact_type=fact_type,
                    label=label,
                    value=value,
                    confidence=confidence,
                )

        for value in _extract_data_paths_from_line(line):
            _append_fact(
                facts,
                seen_keys,
                fact_type="data_path",
                label="Data path",
                value=value,
                confidence=0.9,
            )

    return facts


def extract_local_rules(session_messages: list[dict]) -> list[dict]:
    """Extract environment facts from high-signal user/assistant text only."""
    all_text: list[str] = []
    for msg in session_messages:
        role = msg.get("role", "")
        if role not in {"user", "assistant"}:
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            all_text.append(content)
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type", "text") == "text"
                and isinstance(block.get("text"), str)
            ):
                all_text.append(block["text"])

    combined = "\n".join(all_text)
    return extract_facts_from_text(combined)


def _append_fact(
    facts: list[dict],
    seen_keys: set[str],
    *,
    fact_type: str,
    label: str,
    value: str,
    confidence: float,
) -> None:
    key = f"{fact_type}:{value}"
    if key in seen_keys:
        return
    seen_keys.add(key)
    facts.append(
        {
            "key": key,
            "type": fact_type,
            "label": label,
            "value": value,
            "confidence": confidence,
        }
    )


def _extract_data_paths_from_line(line: str) -> Iterable[str]:
    lowered = line.lower()
    if not any(keyword in lowered for keyword in _DATA_PATH_CONTEXT_KEYWORDS):
        return ()

    values: list[str] = []
    for match in _DATA_PATH_PATTERN.finditer(line):
        value = match.group(1).strip().rstrip(".,;:)")
        lowered_value = value.lower()
        if len(value) < 3:
            continue
        if not any(keyword in lowered_value for keyword in _DATA_PATH_VALUE_KEYWORDS):
            continue
        if any(part in lowered_value for part in _DATA_PATH_DENY_PARTS):
            continue
        values.append(value)
    return values


def facts_to_rules_markdown(facts: list[dict]) -> str:
    """Convert extracted facts to a markdown rules document."""
    if not facts:
        return ""

    grouped: dict[str, list[dict]] = {}
    for fact in facts:
        grouped.setdefault(fact["type"], []).append(fact)

    lines = [
        "# Local Environment Rules",
        "",
        "*Auto-generated from session history. Do not edit manually.*",
        "",
    ]

    section_titles = {
        "ssh_host": "SSH Hosts",
        "data_path": "Data Paths",
        "conda_env": "Python Environments",
        "api_endpoint": "API Endpoints",
        "env_var": "Environment Variables",
        "git_remote": "Git Repositories",
        "ray_cluster": "Ray Cluster Config",
    }

    for fact_type, items in grouped.items():
        title = section_titles.get(fact_type, fact_type.replace("_", " ").title())
        lines.append(f"## {title}")
        lines.append("")
        for item in items:
            lines.append(f"- `{item['value']}`")
        lines.append("")

    return "\n".join(lines)
