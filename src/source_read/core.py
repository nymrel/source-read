"""Represent source-read outcomes without confusing unknown with success.

This module performs no I/O.  Callers remain responsible for consulting a
source and for reporting failures honestly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any, Generic, Iterable, TypeVar
from unicodedata import category

T = TypeVar("T")

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNKNOWN = 2
UNKNOWN = "UNKNOWN"


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _label_text(value: str | PurePath | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, PurePath):
        return value.as_posix()
    if isinstance(value, str):
        return value
    raise TypeError("label must be a string, path, or None")


def _verdict_exit_code(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("verdict_exit_code must be an integer")
    if value not in {EXIT_PASS, EXIT_FAIL}:
        raise ValueError("verdict_exit_code must be EXIT_PASS (0) or EXIT_FAIL (1)")
    return value


def _escape_controls(value: str) -> str:
    """Render control characters as visible escapes for one-line diagnostics."""

    rendered: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\n":
            rendered.append(r"\n")
        elif character == "\r":
            rendered.append(r"\r")
        elif character == "\t":
            rendered.append(r"\t")
        elif codepoint < 32 or codepoint == 127:
            rendered.append(f"\\x{codepoint:02x}")
        elif category(character).startswith("C") or codepoint in {0x2028, 0x2029}:
            escape_width = 4 if codepoint <= 0xFFFF else 8
            marker = "u" if escape_width == 4 else "U"
            rendered.append(f"\\{marker}{codepoint:0{escape_width}x}")
        else:
            rendered.append(character)
    return "".join(rendered)


@dataclass(frozen=True, slots=True)
class SourceRead(Generic[T]):
    """The caller-reported result of consulting one source.

    ``value`` is intentionally excluded from ``repr`` so diagnostic logging of
    the record does not automatically disclose source contents.  Labels and
    reasons are also hidden from ``repr`` because they may contain sensitive
    paths or exception text.
    """

    value: T = field(repr=False)
    readable: bool
    label: str | None = field(default=None, repr=False)
    reason: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.readable, bool):
            raise TypeError("readable must be a bool")
        if self.label is not None and not isinstance(self.label, str):
            raise TypeError("label must be a string or None")
        if self.readable:
            if self.reason is not None:
                raise ValueError("a readable source cannot have a failure reason")
        else:
            _required_text(self.reason, "reason")

    @classmethod
    def ok(cls, value: T, label: str | PurePath | None = None) -> "SourceRead[T]":
        """Record a successful read; an empty ``value`` is still successful."""

        return cls(value=value, readable=True, label=_label_text(label))

    @classmethod
    def unreadable(
        cls,
        label: str | PurePath | None,
        reason: str,
        *,
        empty: T | None = None,
    ) -> "SourceRead[T | None]":
        """Record a source that could not be consulted.

        ``empty`` lets a caller continue non-verdict computation with a neutral
        value.  It never changes the unreadable state.
        """

        return cls(
            value=empty,
            readable=False,
            label=_label_text(label),
            reason=_required_text(reason, "reason"),
        )

    def describe(self, *, include_details: bool = False) -> str:
        """Return a safe summary, or explicitly requested sensitive details."""

        if self.readable:
            return "source readable"
        if not include_details:
            return "source unreadable"
        label = _escape_controls(self.label or "<unlabeled source>")
        reason = _escape_controls(self.reason or "unreadable")
        return f"{label}: {reason}"


@dataclass(frozen=True, slots=True, init=False)
class SourceAssessment:
    """Immutable, single-pass assessment returned by :func:`assess_sources`.

    The result type has no public constructor.  This prevents callers from
    accidentally manufacturing a contradictory assessment while preserving a
    compact, inspectable return value.
    """

    reads: tuple[SourceRead[Any], ...] = field(repr=False)
    problems: tuple[str, ...]
    unreadable_indexes: tuple[int, ...]
    requested_verdict: str = field(repr=False)
    effective_verdict: str = field(repr=False)
    requested_exit_code: int
    exit_code: int
    allow_empty: bool
    empty_input: bool
    unknown_applied: bool

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TypeError("SourceAssessment objects are created by assess_sources()")

    @classmethod
    def _create(
        cls,
        *,
        reads: tuple[SourceRead[Any], ...],
        problems: tuple[str, ...],
        unreadable_indexes: tuple[int, ...],
        requested_verdict: str,
        effective_verdict: str,
        requested_exit_code: int,
        exit_code: int,
        allow_empty: bool,
        empty_input: bool,
        unknown_applied: bool,
    ) -> "SourceAssessment":
        result = object.__new__(cls)
        values = {
            "reads": reads,
            "problems": problems,
            "unreadable_indexes": unreadable_indexes,
            "requested_verdict": requested_verdict,
            "effective_verdict": effective_verdict,
            "requested_exit_code": requested_exit_code,
            "exit_code": exit_code,
            "allow_empty": allow_empty,
            "empty_input": empty_input,
            "unknown_applied": unknown_applied,
        }
        for name, value in values.items():
            object.__setattr__(result, name, value)
        return result

    def detailed_problems(self) -> tuple[str, ...]:
        """Return labels/reasons after an explicit disclosure decision.

        Callers must sanitize the stored labels and reasons before sending this
        output to logs, telemetry, issue trackers, or other people.
        """

        if self.empty_input and self.unknown_applied:
            return ("no sources were provided",)
        return tuple(
            f"source[{index}] {self.reads[index].describe(include_details=True)}"
            for index in self.unreadable_indexes
        )


def assess_sources(
    reads: Iterable[SourceRead[Any]],
    verdict: str,
    *,
    verdict_exit_code: int = EXIT_PASS,
    allow_empty: bool = False,
) -> SourceAssessment:
    """Assess ``reads`` exactly once and return a stable result.

    A zero-source input is unknown by default.  ``allow_empty=True`` is an
    explicit policy decision that permits the caller's verdict to pass through.
    Exceptions raised while iterating ``reads`` propagate; callers should turn
    a failed source operation into :meth:`SourceRead.unreadable` instead of
    treating an interrupted iterable as complete.
    """

    requested_verdict = _required_text(verdict, "verdict")
    if requested_verdict.strip().casefold() == UNKNOWN.casefold():
        raise ValueError("verdict must not use the reserved UNKNOWN result")
    requested_exit_code = _verdict_exit_code(verdict_exit_code)
    if not isinstance(allow_empty, bool):
        raise TypeError("allow_empty must be a bool")

    materialized = tuple(reads)
    for index, read in enumerate(materialized):
        if not isinstance(read, SourceRead):
            raise TypeError(f"reads[{index}] must be a SourceRead")

    empty_input = not materialized
    unreadable_indexes = tuple(
        index for index, read in enumerate(materialized) if not read.readable
    )
    empty_is_unknown = empty_input and not allow_empty
    unknown_applied = empty_is_unknown or bool(unreadable_indexes)

    if empty_is_unknown:
        problems = ("no sources were provided",)
    else:
        problems = tuple(
            f"source[{index}] is unreadable" for index in unreadable_indexes
        )

    return SourceAssessment._create(
        reads=materialized,
        problems=problems,
        unreadable_indexes=unreadable_indexes,
        requested_verdict=requested_verdict,
        effective_verdict=UNKNOWN if unknown_applied else requested_verdict,
        requested_exit_code=requested_exit_code,
        exit_code=EXIT_UNKNOWN if unknown_applied else requested_exit_code,
        allow_empty=allow_empty,
        empty_input=empty_input,
        unknown_applied=unknown_applied,
    )
