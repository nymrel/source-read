"""Build and install the package using a hash-locked, offline build phase."""

from __future__ import annotations

import base64
import csv
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
from verify_release import PROJECT_VERSION, verify_tree

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
    with zipfile.ZipFile(wheel) as bundle:
        names = set(bundle.namelist())
        expected = {
            "source_read/__init__.py",
            "source_read/core.py",
            f"{prefix}METADATA",
            f"{prefix}WHEEL",
            f"{prefix}RECORD",
            f"{prefix}licenses/LICENSE",
        }
        if names != expected:
            raise RuntimeError(
                f"wheel inventory mismatch; missing={sorted(expected - names)} "
                f"extra={sorted(names - expected)}"
            )
        if bundle.read("source_read/__init__.py") != (
            ROOT / "src" / "source_read" / "__init__.py"
        ).read_bytes():
            raise RuntimeError("installed package initializer differs from source")
        if bundle.read("source_read/core.py") != (
            ROOT / "src" / "source_read" / "core.py"
        ).read_bytes():
            raise RuntimeError("installed package core differs from source")
        if bundle.read(f"{prefix}licenses/LICENSE") != (ROOT / "LICENSE").read_bytes():
            raise RuntimeError("wheel license differs from reviewed license")
        metadata = bundle.read(f"{prefix}METADATA").decode("utf-8")
        wheel_metadata = bundle.read(f"{prefix}WHEEL").decode("utf-8")
        record = bundle.read(f"{prefix}RECORD").decode("utf-8")
    if f"Version: {PROJECT_VERSION}\n" not in metadata:
        raise RuntimeError("wheel metadata version mismatch")
    if "Requires-Dist:" in metadata:
        raise RuntimeError("runtime wheel unexpectedly declares dependencies")
    if "Tag: py3-none-any\n" not in wheel_metadata:
        raise RuntimeError("wheel compatibility tag mismatch")

    rows = {row[0]: row[1:] for row in csv.reader(io.StringIO(record))}
    if set(rows) != expected:
        raise RuntimeError("wheel RECORD inventory mismatch")
    for name in sorted(expected):
        digest, size = rows[name]
        if name == f"{prefix}RECORD":
            if digest or size:
                raise RuntimeError("wheel RECORD must not hash itself")
            continue
        payload = zipfile.Path(wheel, at=name).read_bytes()
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
