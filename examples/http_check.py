"""Reference check for a controlled HTTP JSON inventory.

Policy: an HTTP 200 response is the controlled source's complete inventory;
an empty inventory passes and any item is a known failure. Partial, unavailable,
or malformed responses remain unknown. This example performs I/O; Source Read's core
does not. It is not a crawler, authentication client, or network sandbox. Use it
only with endpoints you are authorized to query; it follows ordinary urllib
transport, redirect, proxy, and TLS behavior.
"""

from __future__ import annotations

import argparse
from http.client import HTTPException
import json
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import urlopen

from source_read import SourceAssessment, SourceRead, assess_check

MAX_RESPONSE_BYTES = 1024 * 1024
TIMEOUT_SECONDS = 5


def _reject_nonfinite(_value: str) -> None:
    raise ValueError("inventory must use finite JSON values")


def read_inventory(url: str) -> SourceRead[list[object]]:
    """Read one controlled endpoint, retaining failure separately from empty data."""

    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(ord(character) < 33 or ord(character) == 127 for character in url)
        ):
            raise ValueError("an absolute HTTP(S) URL without credentials is required")
        parsed.port  # Reject malformed port syntax before consulting the source.
        with urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                raise ValueError("a complete HTTP 200 inventory is required")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("inventory exceeds the reference check's size limit")
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite)
        if not isinstance(value, list):
            raise ValueError("inventory must be a JSON array")
    except (OSError, ValueError, RecursionError, HTTPException) as error:
        # HTTPError is also a response object; release its stream explicitly.
        if isinstance(error, HTTPError):
            error.close()
        # Retain a fixed reason rather than transport messages or response text.
        # Even this label/reason stays internal: to_report() omits diagnostics.
        return SourceRead.unreadable(url, "inventory could not be read", empty=[])
    return SourceRead.ok(value, label=url)


def assess_inventory(url: str) -> SourceAssessment:
    read = read_inventory(url)
    # A neutral [] after failure cannot turn the result into PASS: the unreadable
    # record still forces UNKNOWN in assess_check().
    return assess_check([read], passed=len(read.value) == 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--url", help="controlled HTTP(S) JSON inventory endpoint")
    source.add_argument("--no-sources", action="store_true", help="demonstrate absent-source handling")
    args = parser.parse_args(argv)
    assessment = (
        assess_check([], passed=True)
        if args.no_sources
        else assess_inventory(args.url)
    )
    print(json.dumps(assessment.to_report(), sort_keys=True))
    return assessment.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
