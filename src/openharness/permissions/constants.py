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
        # Navigation and shell introspection
        "cd", "pwd", "alias", "unalias", "printenv", "history",  # "env",
        # File listing, search, and text inspection
        "ls", "tree", "find", "locate", "updatedb", "cat", "tail", "head", "less", "more",
        "grep", "rg", "cut", "sort", "uniq", "wc", "diff", "cmp", "comm", "paste", "column", "nl",
        # Binary and file metadata inspection
        "file", "name", "which", "whereis", "type", "strings", "objdump", "readelf",
        "stat", "realpath", "basename", "dirname", "readlink", "hexdump", "xxd", "od",
        "md5sum", "sha1sum", "sha256sum", "sha512sum",
        # System and user info
        "date", "uptime", "whoami", "echo", "id", "df", "du", "free", "top", "htop", "ps", "lsof",
        # Network inspection
        "ifconfig", "ip", "netstat", "ss", "dig", "nslookup", "netcat", "nc",
        # Tracing
        "strace",
    }
)

SAFE_BASH_ALWAYS_PREFIXES: frozenset[str] = frozenset(
    {
        # Git read-only inspection
        "git status", "git diff", "git log", "git branch", "git show", "git fetch", "git remote",
        "git rev-parse", "git grep", "git blame", "git describe", "git rev-list", "git merge-base",
        "git ls-files", "git ls-tree", "git shortlog", "git reflog",
        # Python/package inspection
        "ruff check", "pip list", "pip show", "python --version", "python -V",
        # Node/package inspection
        "npm list", "npm ls", "npm config", "node --version", "node -v",
    }
)

GIT_PAGER_DISABLE_MARKERS: frozenset[str] = frozenset(
    {"--no-pager", "git_pager=cat", "pager=cat", "manpager=cat"}
)

AUTO_APPROVED_SINGLE_WORD_COMMANDS: frozenset[str] = frozenset(
    {
        # File listing, search, and text inspection
        "rg", "head", "stat", "realpath", "basename", "dirname", "readlink",
        "hexdump", "xxd", "od", "nl", "comm", "paste", "column",
    }
)

AUTO_APPROVED_BASH_COMMANDS: frozenset[str] = frozenset(
    {
        # Git read-only inspection
        "git branch --show-current",
    }
)

AUTO_APPROVED_BASH_PREFIXES: frozenset[str] = frozenset(
    {
        # Git read-only inspection
        "git status", "git diff", "git log", "git show", "git rev-parse", "git grep", "git blame",
        "git describe", "git rev-list", "git merge-base", "git ls-files", "git ls-tree",
        "git shortlog",
        # Python/package inspection
        "ruff check", "pip list", "pip show", "python --version", "python -V",
        # Node/package inspection
        "npm list", "npm ls", "node --version", "node -v",
    }
)

INSTALL_MARKERS: tuple[str, ...] = (
    # Package installs
    "npm install", "pnpm install", "yarn install", "bun install",
    "pip install", "uv pip install", "poetry install", "cargo install",
    # Project scaffolding
    "create-next-app", "npx create-", "npm create ", "pnpm create ", "yarn create ", "bun create ",
    "npm init ", "pnpm init ", "yarn init ",
)
