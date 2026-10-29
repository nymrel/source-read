"""Build and install the package using a hash-locked, offline build phase."""

from __future__ import annotations

import base64
import csv
from email.parser import Parser
import hashlib
import io
import os
import subprocess
import sys
import tempfile
import venv
import zipfile
from pathlib import Path

from build_source_archive import publish_bytes
from verify_release import PROJECT_VERSION, read_regular_bytes, verify_tree

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "requirements" / "build.lock"
SOURCE_DATE_EPOCH = "315532800"


def _environment_python(environment: Path) -> Path:
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return environment / scripts / executable


def _run(command: list[str], *, cwd: Path, environment: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=environment, check=True)


def _create_environment(path: Path) -> Path:
    venv.EnvBuilder(with_pip=True, clear=False).create(path)
    return _environment_python(path)


def _download_locked_wheels(wheelhouse: Path) -> None:
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "download",
            "--disable-pip-version-check",
            "--require-hashes",
            "--only-binary=:all:",
            "--dest",
            str(wheelhouse),
            "--requirement",
            str(LOCK),
        ],
        cwd=ROOT,
    )


def _install_locked_build_tools(python: Path, wheelhouse: Path) -> None:
    _run(
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "--require-hashes",
            "--only-binary=:all:",
            "--force-reinstall",
            "--requirement",
            str(LOCK),
        ],
        cwd=ROOT,
    )
    _run([str(python), "-m", "pip", "--isolated", "check"], cwd=ROOT)


def _build_wheel(python: Path, destination: Path) -> Path:
    environment = os.environ.copy()
    environment.update(
        {
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
            "PIP_NO_INDEX": "1",
            "PYTHONHASHSEED": "0",
            "SOURCE_DATE_EPOCH": SOURCE_DATE_EPOCH,
        }
    )
    _run(
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--no-index",
            "--no-cache-dir",
            "--wheel-dir",
            str(destination),
            str(ROOT),
        ],
        cwd=destination,
        environment=environment,
    )
    wheels = tuple(destination.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"expected one wheel, found {len(wheels)}")
    return wheels[0]


def _verify_wheel_metadata(wheel: Path) -> None:
    prefix = f"source_read-{PROJECT_VERSION}.dist-info/"
    wheel_bytes = read_regular_bytes(wheel, "wheel")
    with zipfile.ZipFile(io.BytesIO(wheel_bytes)) as bundle:
        names = bundle.namelist()
        if len(names) != len(set(names)):
            raise RuntimeError("wheel contains duplicate entries")
        expected = {
            "source_read/__init__.py",
            "source_read/core.py",
            f"{prefix}METADATA",
            f"{prefix}WHEEL",
            f"{prefix}RECORD",
            f"{prefix}licenses/LICENSE",
        }
        if set(names) != expected:
            raise RuntimeError(
                f"wheel inventory mismatch; missing={sorted(expected - set(names))} "
                f"extra={sorted(set(names) - expected)}"
            )
        payloads = {name: bundle.read(name) for name in names}
        if payloads["source_read/__init__.py"] != (
            ROOT / "src" / "source_read" / "__init__.py"
        ).read_bytes():
            raise RuntimeError("installed package initializer differs from source")
        if payloads["source_read/core.py"] != (
            ROOT / "src" / "source_read" / "core.py"
        ).read_bytes():
            raise RuntimeError("installed package core differs from source")
        if payloads[f"{prefix}licenses/LICENSE"] != (ROOT / "LICENSE").read_bytes():
            raise RuntimeError("wheel license differs from reviewed license")
        metadata = payloads[f"{prefix}METADATA"].decode("utf-8")
        wheel_metadata = payloads[f"{prefix}WHEEL"].decode("utf-8")
        record = payloads[f"{prefix}RECORD"].decode("utf-8")
    parsed_metadata = Parser().parsestr(metadata)
    expected_fields = {
        "Metadata-Version": "2.5",
        "Name": "source-read",
        "Version": PROJECT_VERSION,
        "Summary": "Keep unreadable sources from being mistaken for clean results.",
        "Author": "Nymrel",
        "License-Expression": "MIT",
        "License-File": "LICENSE",
        "Keywords": "source-read,tri-state,truthfulness,validation",
        "Requires-Python": ">=3.11",
        "Description-Content-Type": "text/markdown",
    }
    for field, expected_value in expected_fields.items():
        if parsed_metadata.get_all(field, []) != [expected_value]:
            raise RuntimeError(f"wheel metadata field mismatch: {field}")
    expected_urls = [
        "Homepage, https://github.com/nymrel/source-read",
        "Source, https://github.com/nymrel/source-read",
        "Issues, https://github.com/nymrel/source-read/issues",
    ]
    if parsed_metadata.get_all("Project-URL", []) != expected_urls:
        raise RuntimeError("wheel project URLs differ from reviewed metadata")
    expected_classifiers = [
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Software Development :: Quality Assurance",
    ]
    if parsed_metadata.get_all("Classifier", []) != expected_classifiers:
        raise RuntimeError("wheel classifiers differ from reviewed metadata")
    if parsed_metadata.get_all("Requires-Dist"):
        raise RuntimeError("runtime wheel unexpectedly declares dependencies")
    if parsed_metadata.get_payload().encode("utf-8") != (ROOT / "README.md").read_bytes():
        raise RuntimeError("wheel README payload differs from reviewed source")
    if wheel_metadata != (
        "Wheel-Version: 1.0\n"
        "Generator: hatchling 1.32.3\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ):
        raise RuntimeError("wheel compatibility metadata mismatch")

    rows = list(csv.reader(io.StringIO(record)))
    if any(len(row) != 3 for row in rows):
        raise RuntimeError("wheel RECORD row shape mismatch")
    row_names = [row[0] for row in rows]
    if len(row_names) != len(set(row_names)):
        raise RuntimeError("wheel RECORD contains duplicate rows")
    if set(row_names) != expected:
        raise RuntimeError("wheel RECORD inventory mismatch")
    record_entries = {row[0]: row[1:] for row in rows}
    for name in sorted(expected):
        digest, size = record_entries[name]
        if name == f"{prefix}RECORD":
            if digest or size:
                raise RuntimeError("wheel RECORD must not hash itself")
            continue
        payload = payloads[name]
        encoded = base64.urlsafe_b64encode(hashlib.sha256(payload).digest()).rstrip(b"=")
        if digest != f"sha256={encoded.decode('ascii')}" or size != str(len(payload)):
            raise RuntimeError(f"wheel RECORD mismatch: {name}")


def _verify_runtime_install(runtime: Path, wheel: Path) -> None:
    python = _create_environment(runtime)
    _run(
        [
            str(python),
            "-m",
            "pip",
            "--isolated",
            "install",
            "--disable-pip-version-check",
            "--no-index",
            "--no-deps",
            str(wheel),
        ],
        cwd=runtime,
    )
    smoke = (
        "import importlib.metadata as m, pathlib, source_read, sys; "
        f"assert source_read.__version__ == '{PROJECT_VERSION}'; "
        "assert m.version('source-read') == source_read.__version__; "
        "assert not m.requires('source-read'); "
        "a = source_read.assess_check([source_read.SourceRead.ok([])], passed=False); "
        "assert (a.effective_verdict, a.exit_code, a.is_fail) == ('FAIL', 1, True); "
        "assert pathlib.Path(source_read.__file__).resolve().is_relative_to("
        "pathlib.Path(sys.prefix).resolve())"
    )
    _run([str(python), "-I", "-c", smoke], cwd=runtime)


def main() -> int:
    verify_tree(ROOT)
    (ROOT / "dist").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
        workspace = Path(temporary)
        wheelhouse = workspace / "wheelhouse"
        wheelhouse.mkdir()
        _download_locked_wheels(wheelhouse)

        built: list[Path] = []
        for number in (1, 2):
            build_python = _create_environment(workspace / f"build-{number}")
            _install_locked_build_tools(build_python, wheelhouse)
            destination = workspace / f"wheel-{number}"
            destination.mkdir()
            built.append(_build_wheel(build_python, destination))

        first = built[0].read_bytes()
        second = built[1].read_bytes()
        if first != second:
            raise RuntimeError("repeated locked wheel builds differ")
        _verify_wheel_metadata(built[0])
        _verify_runtime_install(workspace / "runtime", built[0])
        digest = hashlib.sha256(first).hexdigest()
        published = publish_bytes(
            ROOT,
            ROOT / "dist" / built[0].name,
            first,
            _verify_wheel_metadata,
        )

    print(f"locked wheel build: PASS (sha256: {digest})")
    print(f"verified local wheel: {published}")
    print("runtime dependencies: 0")
    print("package registry publication configured: no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
