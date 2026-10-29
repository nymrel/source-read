"""Verify the exact public source tree using only the standard library."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import os
import re
import stat
import sys
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

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
    "requirements/build.in",
    "requirements/build.lock",
    "SECURITY.md",
    "THREAT_MODEL.md",
    "pyproject.toml",
    "scripts/build_source_archive.py",
    "scripts/verify_package_build.py",
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
PROJECT_VERSION = "0.2.0"
ARCHIVE_PREFIX = f"source-read-{PROJECT_VERSION}"
LICENSE_SHA256 = "46ade72a7e17940f952941f334ce77a9ad8305c20c75099680f487f9af3a413c"
LOCKED_BUILD_REQUIREMENTS = {
    "hatchling": "1.32.3",
    "packaging": "26.3",
    "pathspec": "1.1.1",
    "pip": "26.2.1",
    "pluggy": "1.7.0",
    "tomlkit": "0.15.2",
    "trove-classifiers": "2026.9.21.13",
}


class VerificationError(RuntimeError):
    pass


def _files_under(root: Path) -> set[str]:
    files: set[str] = set()
    for current_text, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        current = Path(current_text)
        kept_directories: list[str] = []
        for name in directory_names:
            if name in IGNORED_PARTS:
                continue
            path = current / name
            relative = path.relative_to(root)
            source_stat = path.lstat()
            if stat.S_ISLNK(source_stat.st_mode) or _is_windows_reparse_point(
                source_stat
            ):
                raise VerificationError(f"tree contains a link: {relative.as_posix()}")
            if not stat.S_ISDIR(source_stat.st_mode):
                raise VerificationError(
                    f"tree contains a non-directory parent: {relative.as_posix()}"
                )
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in file_names:
            path = current / name
            relative = path.relative_to(root)
            if path.suffix == ".pyc":
                continue
            source_stat = path.lstat()
            if stat.S_ISLNK(source_stat.st_mode) or _is_windows_reparse_point(
                source_stat
            ):
                raise VerificationError(f"tree contains a link: {relative.as_posix()}")
            if not stat.S_ISREG(source_stat.st_mode):
                raise VerificationError(
                    f"tree contains a non-regular file: {relative.as_posix()}"
                )
            if source_stat.st_nlink != 1:
                raise VerificationError(
                    f"tree contains a hardlinked file: {relative.as_posix()}"
                )
            files.add(relative.as_posix())
    return files


def _is_windows_reparse_point(source_stat: os.stat_result) -> bool:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(source_stat, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _public_path(root: Path, relative: str) -> tuple[Path, os.stat_result]:
    public_path = PurePosixPath(relative)
    if public_path.is_absolute() or ".." in public_path.parts:
        raise VerificationError(f"invalid public path: {relative}")

    current = root
    final_stat: os.stat_result | None = None
    for index, part in enumerate(public_path.parts):
        current /= part
        source_stat = current.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or _is_windows_reparse_point(source_stat):
            raise VerificationError(f"public path contains a link: {relative}")
        if index < len(public_path.parts) - 1:
            if not stat.S_ISDIR(source_stat.st_mode):
                raise VerificationError(f"public path parent is not a directory: {relative}")
        elif not stat.S_ISREG(source_stat.st_mode):
            raise VerificationError(f"public path is not a regular file: {relative}")
        elif source_stat.st_nlink != 1:
            raise VerificationError(f"public path is hardlinked: {relative}")
        final_stat = source_stat

    if final_stat is None:
        raise VerificationError(f"invalid public path: {relative}")
    return current, final_stat


def read_public_bytes(root: Path, relative: str) -> bytes:
    path, expected_stat = _public_path(root, relative)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(opened_stat.st_mode):
            raise VerificationError(f"public path is not a regular file: {relative}")
        if opened_stat.st_nlink != 1:
            raise VerificationError(f"public path is hardlinked: {relative}")
        if (opened_stat.st_dev, opened_stat.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise VerificationError(f"public path changed while reading: {relative}")
        return source.read()


def read_regular_bytes(path: Path, description: str) -> bytes:
    """Read one unlinked regular file exactly once with identity checks."""

    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    expected_stat: os.stat_result | None = None
    for index, part in enumerate(absolute.parts[1:]):
        current /= part
        source_stat = current.lstat()
        if stat.S_ISLNK(source_stat.st_mode) or _is_windows_reparse_point(source_stat):
            raise VerificationError(f"{description} path contains a link")
        if index < len(absolute.parts) - 2:
            if not stat.S_ISDIR(source_stat.st_mode):
                raise VerificationError(f"{description} parent is not a directory")
        elif not stat.S_ISREG(source_stat.st_mode):
            raise VerificationError(f"{description} is not a regular file")
        elif source_stat.st_nlink != 1:
            raise VerificationError(f"{description} is hardlinked")
        expected_stat = source_stat

    if expected_stat is None:
        raise VerificationError(f"invalid {description} path")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(absolute, flags)
    with os.fdopen(descriptor, "rb") as source:
        opened_stat = os.fstat(source.fileno())
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
            raise VerificationError(f"{description} is not an unlinked regular file")
        if (opened_stat.st_dev, opened_stat.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ):
            raise VerificationError(f"{description} changed while reading")
        return source.read()


def _parse_build_lock(source: str) -> dict[str, tuple[str, str]]:
    locked: dict[str, tuple[str, str]] = {}
    pattern = re.compile(
        r"([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s]+) --hash=sha256:([0-9a-f]{64})"
    )
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = pattern.fullmatch(stripped)
        if not match:
            raise VerificationError(f"invalid build lock entry: {stripped}")
        name = match.group(1).replace("_", "-").casefold()
        if name in locked:
            raise VerificationError(f"duplicate build lock entry: {name}")
        locked[name] = (match.group(2), match.group(3))
    return locked


def _read_public_text(root: Path, relative: str) -> str:
    return read_public_bytes(root, relative).decode("utf-8")


def _check_imports(source: str, filename: str) -> None:
    tree = ast.parse(source, filename=filename)
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
    license_digest = hashlib.sha256(read_public_bytes(root, "LICENSE")).hexdigest()
    if license_digest != LICENSE_SHA256:
        raise VerificationError("LICENSE differs from the reviewed Nymrel MIT notice")

    public_text = "\n".join(
        _read_public_text(root, relative) for relative in PUBLIC_FILES
    )
    for pattern in FORBIDDEN_PUBLIC_PATTERNS:
        if pattern.search(public_text):
            raise VerificationError(f"private marker in public file: {pattern.pattern}")

    metadata = tomllib.loads(_read_public_text(root, "pyproject.toml"))
    if metadata.get("build-system") != {
        "requires": ["hatchling==1.32.3"],
        "build-backend": "hatchling.build",
    }:
        raise VerificationError("unexpected build backend")
    project = metadata["project"]
    if project.get("name") != "source-read" or project.get("version") != PROJECT_VERSION:
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
        "/requirements",
        "/SECURITY.md",
        "/THREAT_MODEL.md",
        "/pyproject.toml",
        "/scripts",
        "/src",
        "/tests",
    ]:
        raise VerificationError("unexpected source-distribution allowlist")

    init_text = _read_public_text(root, "src/source_read/__init__.py")
    if f'__version__ = "{PROJECT_VERSION}"' not in init_text:
        raise VerificationError("runtime and metadata versions differ")
    build_input = _read_public_text(root, "requirements/build.in")
    if build_input != (
        "# Top-level tools for the reviewed build environment.\n"
        "pip==26.2.1\n"
        "hatchling==1.32.3\n"
    ):
        raise VerificationError("unexpected top-level build requirements")
    locked = _parse_build_lock(_read_public_text(root, "requirements/build.lock"))
    versions = {name: version for name, (version, _digest) in locked.items()}
    if versions != LOCKED_BUILD_REQUIREMENTS:
        raise VerificationError("build lock does not match the reviewed dependency closure")
    _check_imports(
        _read_public_text(root, "src/source_read/core.py"),
        "src/source_read/core.py",
    )

    if _read_public_text(root, ".github/CODEOWNERS") != "* @JalenBuildsHub\n":
        raise VerificationError("unexpected CODEOWNERS")
    if _read_public_text(root, ".gitattributes") != "* text=auto eol=lf\n":
        raise VerificationError("unexpected line-ending policy")
    if _read_public_text(root, ".gitignore") != (
        "__pycache__/\n*.py[cod]\ndist/\nbuild/\n.venv/\n*.egg-info/\n"
    ):
        raise VerificationError("unexpected public ignore policy")

    workflow = _read_public_text(root, ".github/workflows/ci.yml")
    required_workflow_fragments = (
        "permissions:\n  contents: read",
        "persist-credentials: false",
        "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
        "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97",
        "python scripts/verify_package_build.py",
        "python scripts/build_source_archive.py --check-reproducible",
        "python scripts/verify_release.py --archive dist/source-read-0.2.0.zip",
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


def canonical_archive_bytes(root: Path) -> bytes:
    root = root.resolve()
    output = io.BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_STORED,
    ) as bundle:
        for relative in PUBLIC_FILES:
            info = zipfile.ZipInfo(
                filename=f"{ARCHIVE_PREFIX}/{relative}",
                date_time=(1980, 1, 1, 0, 0, 0),
            )
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, read_public_bytes(root, relative))
    return output.getvalue()


def verify_archive_bytes(root: Path, archive_bytes: bytes) -> None:
    expected_order = [f"{ARCHIVE_PREFIX}/{name}" for name in PUBLIC_FILES]
    expected_names = set(expected_order)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as bundle:
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
            if info.compress_type != zipfile.ZIP_STORED:
                raise VerificationError(f"unexpected compression method: {info.filename}")
            if info.extra or info.comment:
                raise VerificationError(f"archive entry has unreviewed metadata: {info.filename}")
            relative = info.filename.removeprefix(f"{ARCHIVE_PREFIX}/")
            if bundle.read(info) != read_public_bytes(root, relative):
                raise VerificationError(f"archive content mismatch: {relative}")
        if bundle.comment:
            raise VerificationError("archive has an unreviewed comment")
    if archive_bytes != canonical_archive_bytes(root):
        raise VerificationError("archive bytes differ from the canonical deterministic build")


def verify_archive(root: Path, archive: Path) -> None:
    verify_archive_bytes(root, read_regular_bytes(archive, "archive"))


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
