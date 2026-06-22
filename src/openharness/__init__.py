"""OpenHarness core package."""

# Suppress SyntaxWarning from jieba on Python 3.12+ (upstream is unmaintained).
# Must be set before jieba is imported anywhere. Compile-time warnings do not
# reliably carry the jieba module name, so avoid a module-scoped filter here.
import warnings

warnings.filterwarnings(
    "ignore",
    message=r"invalid escape sequence '\\[.s]'",
    category=SyntaxWarning,
)
