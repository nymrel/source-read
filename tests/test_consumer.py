from __future__ import annotations

import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
SPEC = importlib.util.spec_from_file_location("http_check_example", ROOT / "examples/http_check.py")
assert SPEC is not None and SPEC.loader is not None
consumer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(consumer)

SECRET = "synthetic-private-token"
CONTROL_VALUE = "synthetic-archive-value\x1b[31m\n\u202e"


class ConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        payloads = {
            "/empty": (200, b"[]"),
            "/partial": (206, b"[]"),
            "/findings": (200, json.dumps([{"private": CONTROL_VALUE}]).encode()),
            "/unavailable": (503, SECRET.encode()),
            "/invalid": (200, ("not JSON: " + SECRET).encode()),
            "/invalid-utf8": (200, b"\xff\xfe" + SECRET.encode()),
            "/wrong-shape": (200, json.dumps({"private": SECRET}).encode()),
            "/nonfinite": (200, b"[NaN]"),
            "/oversize": (200, b" " * (consumer.MAX_RESPONSE_BYTES + 1)),
        }

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, body = payloads.get(self.path.split("?", 1)[0], (404, b"missing"))
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def log_message(self, *_args: object) -> None:
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def run_check(self, *arguments: str) -> tuple[int, dict[str, object]]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = consumer.main(list(arguments))
        self.assertEqual(stderr.getvalue(), "")
        emitted = stdout.getvalue()
        report = json.loads(emitted)
        self.assertEqual(report["schema"], "source-read.assessment.v1")
        for private in (SECRET, "synthetic-archive-value", CONTROL_VALUE, self.base_url, "not JSON", "private", "traceback"):
            self.assertNotIn(private, emitted)
        self.assertEqual(report["exit_code"], code)
        return code, report

    def test_readable_empty_inventory_passes(self) -> None:
        code, report = self.run_check("--url", self.base_url + "/empty?token=" + SECRET)
        self.assertEqual((code, report["outcome"]), (0, "PASS"))
        self.assertEqual(report["source_count"], 1)
        self.assertEqual(report["unreadable_indexes"], [])

    def test_finding_inventory_is_known_failure_without_disclosing_content(self) -> None:
        self.assertIn(chr(27), CONTROL_VALUE)
        self.assertIn(chr(10), CONTROL_VALUE)
        self.assertIn(chr(0x202E), CONTROL_VALUE)
        self.assertNotIn(chr(92), CONTROL_VALUE)
        code, report = self.run_check("--url", self.base_url + "/findings?token=" + SECRET)
        self.assertEqual((code, report["outcome"]), (1, "FAIL"))
        self.assertEqual(report["unreadable_indexes"], [])

    def test_partial_empty_inventory_cannot_masquerade_as_complete(self) -> None:
        code, report = self.run_check("--url", self.base_url + "/partial")
        self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))
        self.assertEqual(report["unreadable_indexes"], [0])
        self.assertTrue(report["unknown_applied"])

    def test_source_failure_cannot_masquerade_as_empty_inventory(self) -> None:
        code, report = self.run_check("--url", self.base_url + "/unavailable?token=" + SECRET)
        self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))
        self.assertEqual(report["unreadable_indexes"], [0])
        self.assertTrue(report["unknown_applied"])

    def test_http_failure_response_is_closed_without_disclosing_details(self) -> None:
        body = io.BytesIO(SECRET.encode())
        error = HTTPError(self.base_url + "/empty", 503, SECRET, {}, body)
        with mock.patch.object(consumer, "urlopen", side_effect=error):
            code, report = self.run_check("--url", self.base_url + "/empty")
        self.assertTrue(body.closed)
        self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))

    def test_malformed_or_unsupported_inventory_remains_unknown(self) -> None:
        for endpoint in ("invalid", "invalid-utf8", "wrong-shape", "nonfinite", "oversize"):
            with self.subTest(endpoint=endpoint):
                code, report = self.run_check("--url", self.base_url + "/" + endpoint)
                self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))
                self.assertEqual(report["unreadable_indexes"], [0])

    def test_zero_sources_remain_unknown_without_making_a_request(self) -> None:
        with mock.patch.object(consumer, "urlopen") as opener:
            code, report = self.run_check("--no-sources")
        opener.assert_not_called()
        self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))
        self.assertEqual(report["source_count"], 0)
        self.assertEqual(report["unreadable_indexes"], [])
        self.assertTrue(report["empty_input"])

    def test_unsupported_or_credential_urls_are_not_opened(self) -> None:
        for url in (
            "file:///synthetic-private-token",
            "http://user:synthetic-private-token@127.0.0.1/empty",
            "http://127.0.0.1:bad/empty",
            "http://127.0.0.1/\nsynthetic-private-token",
        ):
            with self.subTest(url=url), mock.patch.object(consumer, "urlopen") as opener:
                code, report = self.run_check("--url", url)
                self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))
                opener.assert_not_called()

    def test_transport_exception_details_are_not_emitted(self) -> None:
        with mock.patch.object(consumer, "urlopen", side_effect=TimeoutError(SECRET)):
            code, report = self.run_check("--url", self.base_url + "/empty")
        self.assertEqual((code, report["outcome"]), (2, "UNKNOWN"))

    def test_decoder_resource_failure_preserves_unreadable_state(self) -> None:
        with mock.patch.object(consumer.json, "loads", side_effect=RecursionError(SECRET)):
            read = consumer.read_inventory(self.base_url + "/empty")
        self.assertFalse(read.readable)
        self.assertEqual(read.value, [])
        self.assertEqual(read.reason, "inventory could not be read")


if __name__ == "__main__":
    unittest.main()
