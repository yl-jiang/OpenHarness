"""Shared project AI helpers: matching, discovery, signals, prompts."""

import warnings

# Suppress SyntaxWarning from jieba on Python 3.12+ before matcher imports it.
# Compile-time warnings do not reliably carry the jieba module name.
warnings.filterwarnings(
    "ignore",
    message=r"invalid escape sequence '\\[.s]'",
    category=SyntaxWarning,
)
