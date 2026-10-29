from __future__ import annotations

import sys
import unittest
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from source_read import (  # noqa: E402
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_UNKNOWN,
    UNKNOWN,
    SourceAssessment,
    SourceRead,
    assess_sources,
)


class SourceReadTests(unittest.TestCase):
    def test_unreadable_source_forces_unknown(self) -> None:
        read = SourceRead.unreadable("inventory", "not available", empty=[])
        assessment = assess_sources([read], "CLEAR")

        self.assertEqual(assessment.effective_verdict, UNKNOWN)
        self.assertEqual(assessment.exit_code, EXIT_UNKNOWN)
        self.assertEqual(assessment.problems, ("source[0] is unreadable",))
        self.assertTrue(assessment.unknown_applied)

    def test_readable_empty_value_keeps_verdict(self) -> None:
        read = SourceRead.ok([], "inventory")
        assessment = assess_sources([read], "CLEAR")

        self.assertEqual(assessment.effective_verdict, "CLEAR")
        self.assertEqual(assessment.exit_code, EXIT_PASS)
        self.assertEqual(assessment.problems, ())

    def test_readable_populated_value_keeps_failure_verdict(self) -> None:
        read = SourceRead.ok([{"id": 1}], "inventory")
        assessment = assess_sources(
            [read], "FAILED", verdict_exit_code=EXIT_FAIL
        )

        self.assertEqual(assessment.effective_verdict, "FAILED")
        self.assertEqual(assessment.exit_code, EXIT_FAIL)

    def test_mixed_reads_force_unknown_in_either_order(self) -> None:
        good = SourceRead.ok([1], "good")
        bad = SourceRead.unreadable("bad", "offline")

        first = assess_sources([bad, good], "CLEAR")
        last = assess_sources([good, bad], "CLEAR")

        self.assertEqual(first.effective_verdict, UNKNOWN)
        self.assertEqual(first.unreadable_indexes, (0,))
        self.assertEqual(last.effective_verdict, UNKNOWN)
        self.assertEqual(last.unreadable_indexes, (1,))

    def test_empty_input_is_unknown_by_default(self) -> None:
        assessment = assess_sources([], "CLEAR")

        self.assertTrue(assessment.empty_input)
        self.assertFalse(assessment.allow_empty)
        self.assertEqual(assessment.effective_verdict, UNKNOWN)
        self.assertEqual(assessment.exit_code, EXIT_UNKNOWN)
        self.assertEqual(assessment.problems, ("no sources were provided",))

    def test_empty_input_requires_explicit_opt_out(self) -> None:
        assessment = assess_sources(
            [], "NOT_APPLICABLE", verdict_exit_code=EXIT_PASS, allow_empty=True
        )

        self.assertTrue(assessment.empty_input)
        self.assertTrue(assessment.allow_empty)
        self.assertFalse(assessment.unknown_applied)
        self.assertEqual(assessment.effective_verdict, "NOT_APPLICABLE")
        self.assertEqual(assessment.exit_code, EXIT_PASS)
        self.assertEqual(assessment.problems, ())

    def test_one_shot_generator_is_consumed_once(self) -> None:
        visits: list[str] = []

        def reads():
            visits.append("unreadable")
            yield SourceRead.unreadable("synthetic", "unavailable")
            visits.append("readable")
            yield SourceRead.ok([], "synthetic-empty")

        assessment = assess_sources(reads(), "CLEAR")

        self.assertEqual(visits, ["unreadable", "readable"])
        self.assertEqual(assessment.problems, ("source[0] is unreadable",))
        self.assertEqual(assessment.effective_verdict, UNKNOWN)
        self.assertEqual(assessment.problems, ("source[0] is unreadable",))
        self.assertEqual(assessment.effective_verdict, UNKNOWN)
        self.assertEqual(visits, ["unreadable", "readable"])

    def test_already_exhausted_generator_is_unknown(self) -> None:
        reads = (item for item in [SourceRead.ok([], "synthetic")])
        self.assertEqual(len(tuple(reads)), 1)

        assessment = assess_sources(reads, "CLEAR")

        self.assertTrue(assessment.empty_input)
        self.assertEqual(assessment.effective_verdict, UNKNOWN)

    def test_invalid_direct_states_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "readable source"):
            SourceRead(value=[], readable=True, reason="failure")
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            SourceRead(value=[], readable=False, reason="  ")
        with self.assertRaisesRegex(TypeError, "reason must be a string"):
            SourceRead(value=[], readable=False, reason=None)
        with self.assertRaisesRegex(TypeError, "readable must be a bool"):
            SourceRead(value=[], readable=1, reason=None)  # type: ignore[arg-type]
        with self.assertRaisesRegex(TypeError, "label must be"):
            SourceRead(value=[], readable=True, label=3)  # type: ignore[arg-type]

    def test_default_diagnostics_do_not_disclose_details_or_values(self) -> None:
        secret_label = "C:/private/customer/account.json"
        secret_reason = "token=do-not-log"
        secret_value = {"customer": "do-not-log"}
        read = SourceRead.unreadable(
            secret_label, secret_reason, empty=secret_value
        )
        assessment = assess_sources([read], "CLEAR")

        safe_outputs = " ".join(
            [repr(read), repr(assessment), read.describe(), *assessment.problems]
        )
        self.assertNotIn(secret_label, safe_outputs)
        self.assertNotIn(secret_reason, safe_outputs)
        self.assertNotIn("customer", safe_outputs)
        self.assertEqual(read.describe(), "source unreadable")

    def test_assessment_repr_hides_caller_verdict_text(self) -> None:
        assessment = assess_sources(
            [SourceRead.ok([], "synthetic")], "PRIVATE\nFORGED"
        )

        self.assertNotIn("PRIVATE", repr(assessment))
        self.assertNotIn("FORGED", repr(assessment))
        self.assertNotIn("\n", repr(assessment))

    def test_assessment_has_no_public_constructor(self) -> None:
        with self.assertRaises(TypeError):
            SourceAssessment()  # type: ignore[call-arg]

    def test_detailed_diagnostics_require_explicit_call(self) -> None:
        read = SourceRead.unreadable("public-name", "synthetic failure")
        assessment = assess_sources([read], "CLEAR")

        self.assertEqual(
            assessment.detailed_problems(),
            ("source[0] public-name: synthetic failure",),
        )

    def test_detailed_diagnostics_escape_control_characters(self) -> None:
        read = SourceRead.unreadable(
            "public\nforged-label\u2028next",
            "failed\r\nforged-entry\tend\x00\u202ereversed",
        )

        detail = assess_sources([read], "CLEAR").detailed_problems()[0]

        self.assertEqual(
            detail,
            r"source[0] public\nforged-label\u2028next: failed\r\nforged-entry\tend\x00\u202ereversed",
        )
        self.assertNotIn("\n", detail)
        self.assertNotIn("\r", detail)
        self.assertNotIn("\t", detail)
        self.assertNotIn("\x00", detail)
        self.assertNotIn("\u2028", detail)
        self.assertNotIn("\u202e", detail)

    def test_iteration_error_propagates(self) -> None:
        class SyntheticIterationError(RuntimeError):
            pass

        def interrupted():
            yield SourceRead.ok([], "first")
            raise SyntheticIterationError("synthetic interruption")

        with self.assertRaisesRegex(
            SyntheticIterationError, "synthetic interruption"
        ):
            assess_sources(interrupted(), "CLEAR")

    def test_path_string_and_absent_labels_format_predictably(self) -> None:
        path_read = SourceRead.unreadable(
            PurePosixPath("safe/example.json"), "not found"
        )
        string_read = SourceRead.unreadable("public-label", "not found")
        absent_read = SourceRead.unreadable(None, "not found")

        self.assertEqual(path_read.label, "safe/example.json")
        self.assertEqual(
            string_read.describe(include_details=True),
            "public-label: not found",
        )
        self.assertEqual(
            absent_read.describe(include_details=True),
            "<unlabeled source>: not found",
        )

    def test_non_records_and_invalid_options_are_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, r"reads\[0\]"):
            assess_sources([object()], "CLEAR")  # type: ignore[list-item]
        with self.assertRaisesRegex(ValueError, "verdict must not be empty"):
            assess_sources([], " ")
        with self.assertRaisesRegex(ValueError, "reserved UNKNOWN"):
            assess_sources([SourceRead.ok([], "synthetic")], " unknown ")
        with self.assertRaisesRegex(TypeError, "allow_empty must be a bool"):
            assess_sources([], "CLEAR", allow_empty=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "EXIT_PASS"):
            assess_sources([], "CLEAR", verdict_exit_code=EXIT_UNKNOWN)
        with self.assertRaisesRegex(ValueError, "EXIT_PASS"):
            assess_sources([], "CLEAR", verdict_exit_code=256)
        with self.assertRaisesRegex(TypeError, "must be an integer"):
            assess_sources([], "CLEAR", verdict_exit_code=True)


if __name__ == "__main__":
    unittest.main()
