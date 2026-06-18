"""OpenHarness core package."""

# Suppress SyntaxWarning from jieba on Python 3.12+ (upstream is unmaintained).
# Must be set before jieba is imported anywhere.
import warnings

warnings.filterwarnings("ignore", category=SyntaxWarning, module=r"jieba.*")
