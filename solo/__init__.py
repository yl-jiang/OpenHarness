"""Standalone solo app built on OpenHarness."""

from solo.agent import OpenHarnessSoloAgent
from solo.commands import (
    SoloCommand,
    extract_solo_content,
    format_process_result,
    parse_backfill_argument,
    parse_solo_command,
    solo_help_text,
)
from solo.models import (
    PendingConfirmation,
    ProcessResult,
    ProfileUpdate,
    SoloConfig,
    SoloEntry,
    SoloRecord,
    SoloReport,
)
from solo.processor import SoloProcessor
from solo.store import SoloStore
from solo.tools import SoloDomainTool, SoloToolRegistry

__all__ = [
    "OpenHarnessSoloAgent",
    "PendingConfirmation",
    "ProcessResult",
    "ProfileUpdate",
    "SoloCommand",
    "SoloConfig",
    "SoloDomainTool",
    "SoloEntry",
    "SoloProcessor",
    "SoloRecord",
    "SoloReport",
    "SoloStore",
    "SoloToolRegistry",
    "extract_solo_content",
    "format_process_result",
    "parse_backfill_argument",
    "parse_solo_command",
    "solo_help_text",
]
