"""Build a deterministic, allowlisted source ZIP using only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import tempfile
from pathlib import Path

from verify_release import (
    ARCHIVE_PREFIX,
    VerificationError,
    _is_windows_reparse_point,
    canonical_archive_bytes,
    verify_archive,
    verify_tree,
)


def _absolute(path: Path) -> Path:
    """Normalize ``.`` and ``..`` without following filesystem links."""

    return Path(os.path.abspath(path))


def _ensure_unlinked_directory(path: Path) -> Path:
    """Create and validate an ordinary output-directory chain."""

    absolute = _absolute(path)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        try:
            source_stat = current.lstat()
        except FileNotFoundError:
            current.mkdir()
            source_stat = current.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or _is_windows_reparse_point(source_stat):
            raise VerificationError(f"output directory contains a link: {current}")
        if not stat.S_ISDIR(source_stat.st_mode):
            raise VerificationError(f"output parent is not a directory: {current}")
    return absolute


def _validate_existing_output(output: Path) -> None:
    try:
        output_stat = output.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(output_stat.st_mode) or _is_windows_reparse_point(output_stat):
        raise VerificationError("output path is a link")
    if not stat.S_ISREG(output_stat.st_mode):
        raise VerificationError("output path is not a regular file")
    if output_stat.st_nlink != 1:
        raise VerificationError("output path is hardlinked")


def _validate_output_location(root: Path, output: Path) -> None:
    """Protect reviewed source files from accidental archive output."""

    try:
        relative = output.relative_to(root)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] != "dist":
        raise VerificationError("output inside the source tree must be under dist/")


def build(root: Path, output: Path) -> str:
    root = root.resolve()
    output = _absolute(output)
    verify_tree(root)
    _validate_output_location(root, output)
    _ensure_unlinked_directory(output.parent)
    _validate_existing_output(output)
    archive_bytes = canonical_archive_bytes(root)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.",
        suffix=".tmp",
        dir=output.parent,
    )
    staged = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(archive_bytes)
            destination.flush()
            os.fsync(destination.fileno())
        os.chmod(staged, 0o644)
        verify_archive(root, staged)
        os.replace(staged, output)
        staged = None
        verify_archive(root, output)
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return hashlib.sha256(archive_bytes).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(f"dist/{ARCHIVE_PREFIX}.zip"),
    )
    parser.add_argument("--check-reproducible", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output if args.output.is_absolute() else root / args.output
    digest = build(root, output)

    if args.check_reproducible:
        # Keep the comparison inside the source workspace. Some managed
        # runtimes expose an OS temp path that is readable but not writable.
        with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
            comparison = Path(temporary) / output.name
            second_digest = build(root, comparison)
            if output.read_bytes() != comparison.read_bytes():
                raise SystemExit("FAIL: repeated source builds differ")
            if digest != second_digest:
                raise SystemExit("FAIL: repeated source digests differ")

    print(f"source archive: {output}")
    print(f"sha256: {digest}")
    print("registry publication configured: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
