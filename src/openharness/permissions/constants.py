"""Constants for permission checking."""

from __future__ import annotations

# Paths that are always denied regardless of permission mode or user config.
# These protect high-value credential and key material from LLM-directed access
# (including via prompt injection).  Patterns use fnmatch syntax and are matched
# against the fully-resolved absolute path produced by the query engine.
SENSITIVE_PATH_PATTERNS: tuple[str, ...] = (
    # SSH keys and config
    "*/.ssh/*",
    # AWS credentials
    "*/.aws/credentials",
    "*/.aws/config",
    # GCP credentials
    "*/.config/gcloud/*",
    # Azure credentials
    "*/.azure/*",
    # GPG keys
    "*/.gnupg/*",
    # Docker credentials
    "*/.docker/config.json",
    # Kubernetes credentials
    "*/.kube/config",
    # OpenHarness own credential stores
    "*/.openharness/credentials.json",
    "*/.openharness/copilot_auth.json",
)

SAFE_SINGLE_WORD_COMMANDS: frozenset[str] = frozenset(
    {
        "cd",
        "ls",
        "cat",
        "pwd",
        "grep",
        "find",
        "tree",
        "wc",
        "objdump",
        "readelf",
        "strings",
        "file",
        "which",
        "whereis",
        "type",
        # "env",
        "printenv",
        "alias",
        "unalias",
        "date",
        "uptime",
        "whoami",
        "echo",
        "id",
        "ifconfig",
        "ip",
        "netstat",
        "ss",
        "df",
        "du",
        "free",
        "top",
        "htop",
        "ps",
        "lsof",
        "netcat",
        "nc",
        "dig",
        "nslookup",
        "strace",
        "locate",
        "updatedb",
        "history",
    }
)

SAFE_BASH_ALWAYS_PREFIXES: frozenset[str] = frozenset(
    {
        "git status",
        "git diff",
        "git log",
        "git branch",
        "git show",
        "git fetch",
        "git remote",
        "git rev-parse",
        "ruff check",
        "pip list",
        "pip show",
        "python --version",
        "python -V",
        "npm list",
        "npm ls",
        "npm config",
        "node --version",
        "node -v",
    }
)

INSTALL_MARKERS: tuple[str, ...] = (
    "npm install",
    "pnpm install",
    "yarn install",
    "bun install",
    "pip install",
    "uv pip install",
    "poetry install",
    "cargo install",
    "create-next-app",
    "npm create ",
    "pnpm create ",
    "yarn create ",
    "bun create ",
    "npx create-",
    "npm init ",
    "pnpm init ",
    "yarn init ",
)
