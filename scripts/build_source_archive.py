"""Build a deterministic, allowlisted source ZIP using only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import tempfile
from pathlib import Path

from verify_release import canonical_archive_bytes, verify_archive, verify_tree


def build(root: Path, output: Path) -> str:
    verify_tree(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_archive_bytes(root))
    verify_archive(root, output)
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/source-read-0.1.0.zip"),
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
