"""Public API for explicit source-read outcomes."""

from .core import (
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_UNKNOWN,
    UNKNOWN,
    SourceAssessment,
    SourceRead,
    assess_check,
    assess_sources,
)

__version__ = "0.2.0"

__all__ = [
    "EXIT_FAIL",
    "EXIT_PASS",
    "EXIT_UNKNOWN",
    "UNKNOWN",
    "SourceAssessment",
    "SourceRead",
    "assess_check",
    "assess_sources",
]
