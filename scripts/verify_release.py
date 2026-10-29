"""Verify the exact public source tree using only the standard library."""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import sys
import tomllib
import zipfile
from pathlib import Path

PUBLIC_FILES = (
    ".gitattributes",
    ".gitignore",
    ".github/CODEOWNERS",
    ".github/workflows/ci.yml",
    "CONTRIBUTING.md",
    "LICENSE",
    "MAINTAINERS.md",
    "PROVENANCE.md",
    "README.md",
    "RELEASING.md",
    "SECURITY.md",
    "THREAT_MODEL.md",
    "pyproject.toml",
    "scripts/build_source_archive.py",
    "scripts/verify_release.py",
    "src/source_read/__init__.py",
    "src/source_read/core.py",
    "tests/__init__.py",
    "tests/test_core.py",
    "tests/test_release.py",
)

LOCAL_ONLY_FILES = ("review/PRIVATE_PROVENANCE.md",)
IGNORED_PARTS = {"__pycache__", ".git", "dist"}
FORBIDDEN_PUBLIC_PATTERNS = (
    re.compile(r"(?i)[a-z]:[\\/](?:users|documents|desktop)[\\/]"),
    re.compile(r"(?i)source (?:blob|commit):\s*[0-9a-f]{40}"),
)
STDLIB_IMPORTS = {"__future__", "dataclasses", "pathlib", "typing", "unicodedata"}
ARCHIVE_PREFIX = "source-read-0.1.0"
LICENSE_SHA256 = "46ade72a7e17940f952941f334ce77a9ad8305c20c75099680f487f9af3a413c"


class VerificationError(RuntimeError):
    pass


def _files_under(root: Path) -> set[str]:
    files: set[str] = set()
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.suffix == ".pyc":
            continue
        files.add(relative.as_posix())
    return files


def _check_imports(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".", 1)[0])
    unexpected = imported - STDLIB_IMPORTS
    if unexpected:
        raise VerificationError(f"non-stdlib runtime imports: {sorted(unexpected)}")


def verify_tree(root: Path) -> None:
    root = root.resolve()
    public = set(PUBLIC_FILES)
    local = set(LOCAL_ONLY_FILES)
    actual = _files_under(root)
    if actual != public and actual != public | local:
        raise VerificationError(
            f"tree allowlist mismatch; missing={sorted(public - actual)} "
            f"extra={sorted(actual - (public | local))}"
        )
    license_digest = hashlib.sha256((root / "LICENSE").read_bytes()).hexdigest()
    if license_digest != LICENSE_SHA256:
        raise VerificationError("LICENSE differs from the reviewed Nymrel MIT notice")

    public_text = "\n".join(
        (root / relative).read_text(encoding="utf-8") for relative in PUBLIC_FILES
    )
    for pattern in FORBIDDEN_PUBLIC_PATTERNS:
        if pattern.search(public_text):
            raise VerificationError(f"private marker in public file: {pattern.pattern}")

    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    if metadata.get("build-system") != {
        "requires": ["hatchling==1.32.3"],
        "build-backend": "hatchling.build",
    }:
        raise VerificationError("unexpected build backend")
    project = metadata["project"]
    if project.get("name") != "source-read" or project.get("version") != "0.1.0":
        raise VerificationError("unexpected package identity")
    if project.get("license") != "MIT" or project.get("license-files") != ["LICENSE"]:
        raise VerificationError("unexpected license metadata")
    if project.get("authors") != [{"name": "Nymrel"}]:
        raise VerificationError("unexpected author metadata")
    if project.get("dependencies") != []:
        raise VerificationError("runtime dependencies must be empty")
    if project.get("requires-python") != ">=3.11" or "scripts" in project:
        raise VerificationError("unexpected runtime or entry-point metadata")
    classifiers = project.get("classifiers", [])
    if "License :: OSI Approved :: MIT License" not in classifiers:
        raise VerificationError("missing MIT classifier")
    if "Private :: Do Not Upload" in classifiers:
        raise VerificationError("stale private classifier")
    if project.get("urls") != {
        "Homepage": "https://github.com/nymrel/source-read",
        "Source": "https://github.com/nymrel/source-read",
        "Issues": "https://github.com/nymrel/source-read/issues",
    }:
        raise VerificationError("unexpected project URLs")
    targets = metadata.get("tool", {}).get("hatch", {}).get("build", {}).get("targets", {})
    if targets.get("wheel", {}).get("packages") != ["src/source_read"]:
        raise VerificationError("unexpected wheel package path")
    if targets.get("sdist", {}).get("include") != [
        "/.gitattributes",
        "/.gitignore",
        "/.github/CODEOWNERS",
        "/.github/workflows/ci.yml",
        "/PROVENANCE.md",
        "/README.md",
        "/RELEASING.md",
        "/CONTRIBUTING.md",
        "/LICENSE",
        "/MAINTAINERS.md",
        "/SECURITY.md",
        "/THREAT_MODEL.md",
        "/pyproject.toml",
        "/scripts",
        "/src",
        "/tests",
    ]:
        raise VerificationError("unexpected source-distribution allowlist")

    init_text = (root / "src/source_read/__init__.py").read_text(encoding="utf-8")
    if '__version__ = "0.1.0"' not in init_text:
        raise VerificationError("runtime and metadata versions differ")
    _check_imports(root / "src/source_read/core.py")

    if (root / ".github/CODEOWNERS").read_text(encoding="utf-8") != "* @JalenBuildsHub\n":
        raise VerificationError("unexpected CODEOWNERS")
    if (root / ".gitattributes").read_text(encoding="utf-8") != "* text=auto eol=lf\n":
        raise VerificationError("unexpected line-ending policy")
    if (root / ".gitignore").read_text(encoding="utf-8") != (
        "__pycache__/\n*.py[cod]\ndist/\nbuild/\n.venv/\n*.egg-info/\n"
    ):
        raise VerificationError("unexpected public ignore policy")

    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    required_workflow_fragments = (
        "permissions:\n  contents: read",
        "persist-credentials: false",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
    )
    for fragment in required_workflow_fragments:
        if fragment not in workflow:
            raise VerificationError(f"workflow requirement missing: {fragment}")
    for forbidden in (
        "pypi",
        "twine",
        "publish",
        "upload-artifact",
        "contents: write",
        "id-token: write",
    ):
        if forbidden in workflow.casefold():
            raise VerificationError(f"publication capability in source CI: {forbidden}")


def verify_archive(root: Path, archive: Path) -> None:
    expected_order = [f"{ARCHIVE_PREFIX}/{name}" for name in PUBLIC_FILES]
    expected_names = set(expected_order)
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        archive_order = [info.filename for info in infos]
        names = set(archive_order)
        if len(infos) != len(names):
            raise VerificationError("archive contains duplicate entry names")
        if names != expected_names:
            raise VerificationError(
                f"archive allowlist mismatch; missing={sorted(expected_names - names)} "
                f"extra={sorted(names - expected_names)}"
            )
        if archive_order != expected_order:
            raise VerificationError("archive entries are not in the reviewed order")
        for info in infos:
            if info.date_time != (1980, 1, 1, 0, 0, 0):
                raise VerificationError(f"nondeterministic timestamp: {info.filename}")
            mode = (info.external_attr >> 16) & 0xFFFF
            if info.create_system != 3 or mode != 0o100644 or info.is_dir():
                raise VerificationError(f"archive entry is not a regular 0644 file: {info.filename}")
            if info.compress_type != zipfile.ZIP_DEFLATED:
                raise VerificationError(f"unexpected compression method: {info.filename}")
            if info.extra or info.comment:
                raise VerificationError(f"archive entry has unreviewed metadata: {info.filename}")
            relative = info.filename.removeprefix(f"{ARCHIVE_PREFIX}/")
            if bundle.read(info) != (root / relative).read_bytes():
                raise VerificationError(f"archive content mismatch: {relative}")
        if bundle.comment:
            raise VerificationError("archive has an unreviewed comment")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    try:
        verify_tree(root)
        if args.archive:
            verify_archive(root, args.archive.resolve())
    except (OSError, KeyError, tomllib.TOMLDecodeError, VerificationError, zipfile.BadZipFile) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    scope = "tree and archive" if args.archive else "tree"
    print(f"release verifier: PASS ({scope}; {len(PUBLIC_FILES)} public files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
