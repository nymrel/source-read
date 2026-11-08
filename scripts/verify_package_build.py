"""Build and install the package using a hash-locked, offline build phase."""

from __future__ import annotations

import base64
import csv
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import venv
import zipfile
import zlib
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


def _verify_deflate_member(compressed: bytes, info: zipfile.ZipInfo) -> None:
    inflater = zlib.decompressobj(-zlib.MAX_WBITS)
    try:
        payload = inflater.decompress(compressed) + inflater.flush()
    except zlib.error as error:
        raise RuntimeError(
            f"wheel compressed payload is invalid: {info.filename}"
        ) from error
    if (
        not inflater.eof
        or inflater.unused_data
        or inflater.unconsumed_tail
        or len(payload) != info.file_size
        or zlib.crc32(payload) & 0xFFFFFFFF != info.CRC
    ):
        raise RuntimeError(f"wheel compressed payload mismatch: {info.filename}")


def _verify_central_directory(
    wheel_bytes: bytes,
    infos: list[zipfile.ZipInfo],
    central_offset: int,
    central_size: int,
    eocd_offset: int,
) -> None:
    """Validate each raw central record without relying on zipfile normalization."""

    cursor = central_offset
    central_end = central_offset + central_size
    for info in infos:
        if cursor + 46 > central_end:
            raise RuntimeError("wheel ZIP central directory mismatch")
        (
            signature,
            version_made_by,
            version_needed,
            flags,
            compression,
            modified_time,
            modified_date,
            crc,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
            comment_length,
            disk_number,
            internal_attr,
            external_attr,
            local_offset,
        ) = struct.unpack_from("<4s2H4H3L5H2L", wheel_bytes, cursor)
        name_start = cursor + 46
        name_end = name_start + name_length
        record_end = name_end + extra_length + comment_length
        expected_name = info.filename.encode("ascii")
        if (
            record_end > central_end
            or signature != b"PK\x01\x02"
            or version_made_by & 0xFF != info.create_version
            or version_made_by >> 8 != info.create_system
            or version_needed & 0xFF != info.extract_version
            or version_needed >> 8 != info.reserved
            or flags != info.flag_bits
            or compression != info.compress_type
            or modified_time != 0
            or modified_date != 33
            or crc != info.CRC
            or compressed_size != info.compress_size
            or uncompressed_size != info.file_size
            or name_length != len(expected_name)
            or wheel_bytes[name_start:name_end] != expected_name
            or extra_length != 0
            or comment_length != 0
            or disk_number != info.volume
            or internal_attr != info.internal_attr
            or external_attr != info.external_attr
            or local_offset != info.header_offset
        ):
            raise RuntimeError(
                f"wheel ZIP central directory mismatch: {info.filename}"
            )
        cursor = record_end
    if cursor != central_end or central_end != eocd_offset:
        raise RuntimeError("wheel ZIP central directory mismatch")


def _verify_wheel_envelope(
    wheel_bytes: bytes,
    bundle: zipfile.ZipFile,
    infos: list[zipfile.ZipInfo],
) -> None:
    """Reject unreferenced bytes and metadata outside wheel member payloads."""

    if not infos or infos[0].header_offset != 0 or bundle.comment:
        raise RuntimeError("wheel ZIP envelope mismatch")

    for index, info in enumerate(infos):
        expected_external = (
            0x81A40000 if info.filename.startswith("source_read/") else 0x01A40000
        )
        expected_create_system = 0 if os.name == "nt" else 3
        if (
            info.date_time != (1980, 1, 1, 0, 0, 0)
            or info.compress_type != zipfile.ZIP_DEFLATED
            or info.flag_bits != 0
            or info.create_system != expected_create_system
            or info.create_version != 20
            or info.extract_version != 20
            or info.reserved != 0
            or info.volume != 0
            or info.internal_attr != 0
            or info.external_attr != expected_external
            or info.extra
            or info.comment
        ):
            raise RuntimeError(f"wheel ZIP member metadata mismatch: {info.filename}")

        offset = info.header_offset
        if offset + 30 > len(wheel_bytes):
            raise RuntimeError("wheel ZIP envelope mismatch")
        (
            signature,
            extract_version,
            flags,
            compression,
            modified_time,
            modified_date,
            crc,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = struct.unpack_from("<4s5H3L2H", wheel_bytes, offset)
        name_start = offset + 30
        name_end = name_start + name_length
        if (
            signature != b"PK\x03\x04"
            or extract_version != info.extract_version
            or flags != info.flag_bits
            or compression != info.compress_type
            or modified_time != 0
            or modified_date != 33
            or crc != info.CRC
            or compressed_size != info.compress_size
            or uncompressed_size != info.file_size
            or wheel_bytes[name_start:name_end] != info.filename.encode("ascii")
            or extra_length != 0
        ):
            raise RuntimeError(f"wheel ZIP local header mismatch: {info.filename}")
        member_end = name_end + extra_length + info.compress_size
        _verify_deflate_member(
            wheel_bytes[name_end + extra_length : member_end], info
        )
        next_offset = (
            infos[index + 1].header_offset
            if index + 1 < len(infos)
            else bundle.start_dir
        )
        if member_end != next_offset:
            raise RuntimeError("wheel ZIP envelope contains unreferenced member bytes")

    eocd_offset = wheel_bytes.rfind(b"PK\x05\x06")
    if eocd_offset < 0 or eocd_offset + 22 > len(wheel_bytes):
        raise RuntimeError("wheel ZIP envelope mismatch")
    (
        signature,
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_length,
    ) = struct.unpack_from("<4s4H2LH", wheel_bytes, eocd_offset)
    expected_central_size = sum(
        46 + len(info.filename.encode("ascii")) for info in infos
    )
    if (
        signature != b"PK\x05\x06"
        or disk_number != 0
        or central_disk != 0
        or disk_entries != len(infos)
        or total_entries != len(infos)
        or central_offset != bundle.start_dir
        or central_size != expected_central_size
        or central_offset + central_size != eocd_offset
        or comment_length != 0
        or eocd_offset + 22 != len(wheel_bytes)
    ):
        raise RuntimeError("wheel ZIP envelope mismatch")
    _verify_central_directory(
        wheel_bytes, infos, central_offset, central_size, eocd_offset
    )


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
        _verify_wheel_envelope(wheel_bytes, bundle, bundle.infolist())
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
        metadata = payloads[f"{prefix}METADATA"]
        wheel_metadata = payloads[f"{prefix}WHEEL"].decode("utf-8")
        record = payloads[f"{prefix}RECORD"].decode("utf-8")
    expected_metadata = (
        "Metadata-Version: 2.5\n"
        "Name: source-read\n"
        f"Version: {PROJECT_VERSION}\n"
        "Summary: Keep unreadable sources from being mistaken for clean results.\n"
        "Project-URL: Homepage, https://github.com/nymrel/source-read\n"
        "Project-URL: Source, https://github.com/nymrel/source-read\n"
        "Project-URL: Issues, https://github.com/nymrel/source-read/issues\n"
        "Author: Nymrel\n"
        "License-Expression: MIT\n"
        "License-File: LICENSE\n"
        "Keywords: source-read,tri-state,truthfulness,validation\n"
        "Classifier: Development Status :: 3 - Alpha\n"
        "Classifier: Intended Audience :: Developers\n"
        "Classifier: License :: OSI Approved :: MIT License\n"
        "Classifier: Programming Language :: Python :: 3\n"
        "Classifier: Programming Language :: Python :: 3 :: Only\n"
        "Classifier: Programming Language :: Python :: 3.11\n"
        "Classifier: Programming Language :: Python :: 3.12\n"
        "Classifier: Programming Language :: Python :: 3.13\n"
        "Classifier: Programming Language :: Python :: 3.14\n"
        "Classifier: Topic :: Software Development :: Quality Assurance\n"
        "Requires-Python: >=3.11\n"
        "Description-Content-Type: text/markdown\n\n"
    ).encode("utf-8") + (ROOT / "README.md").read_bytes()
    if metadata != expected_metadata:
        raise RuntimeError("wheel METADATA differs from reviewed canonical bytes")
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
    if runtime.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("installed consumer must be outside the checkout")
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
        "import importlib.metadata as m, json, pathlib, source_read, sys; "
        f"assert source_read.__version__ == '{PROJECT_VERSION}'; "
        "assert m.version('source-read') == source_read.__version__; "
        "assert not m.requires('source-read'); "
        "a = source_read.assess_check([source_read.SourceRead.ok([])], passed=False); "
        "assert (a.effective_verdict, a.exit_code, a.is_fail) == ('FAIL', 1, True); "
        "assert pathlib.Path(source_read.__file__).resolve().is_relative_to("
        "pathlib.Path(sys.prefix).resolve()); "
        "assert not pathlib.Path(source_read.__file__).resolve().is_relative_to("
        f"pathlib.Path({str(ROOT)!r}).resolve()); "
        "assert a.to_report()['outcome'] == 'FAIL'; "
        "assert json.loads(json.dumps(a.to_report())) == a.to_report()"
    )
    _run([str(python), "-I", "-c", smoke], cwd=runtime)
    privacy_smoke = (
        "import json, source_read; "
        "marker = 'synthetic-installed-private-sentinel\\n\\x1b[31m\\u202e'; "
        "read = source_read.SourceRead.unreadable(marker, marker, empty={'value': marker}); "
        "a = source_read.assess_sources([read], marker, verdict_exit_code=1); "
        "encoded = json.dumps(a.to_report(), ensure_ascii=False); "
        "assert 'synthetic-installed-private-sentinel' not in encoded; "
        "assert '\\x1b' not in encoded and '\\u202e' not in encoded; "
        "assert a.to_report()['outcome'] == 'UNKNOWN' and a.to_report()['exit_code'] == 2"
    )
    _run([str(python), "-I", "-c", privacy_smoke], cwd=runtime)
    _verify_reference_consumer(python, runtime.parent / "consumer", wheel)


def _verify_reference_consumer(python: Path, consumer: Path, wheel: Path) -> None:
    """Exercise the copied reference adapter against synthetic loopback sources."""

    if consumer.resolve().is_relative_to(ROOT.resolve()):
        raise RuntimeError("reference consumer must be outside the checkout")
    consumer.mkdir()
    example = consumer / "http_check.py"
    example.write_bytes((ROOT / "examples/http_check.py").read_bytes())
    sentinel = "synthetic-private-consumer-sentinel\n\x1b[31m\u202e"
    responses = {
        "/empty": (200, b"[]"),
        "/findings": (200, json.dumps([{"private": sentinel}]).encode()),
        "/unavailable": (503, json.dumps({"private": sentinel}).encode()),
        "/invalid": (200, ("invalid JSON " + sentinel).encode()),
        "/shape": (200, json.dumps({"private": sentinel}).encode()),
        "/partial": (206, b"[]"),
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status, body = responses[self.path]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    cases = (
        ("empty", ["--url", f"http://127.0.0.1:{server.server_port}/empty"], 0),
        ("findings", ["--url", f"http://127.0.0.1:{server.server_port}/findings"], 1),
        ("unavailable", ["--url", f"http://127.0.0.1:{server.server_port}/unavailable"], 2),
        ("invalid", ["--url", f"http://127.0.0.1:{server.server_port}/invalid"], 2),
        ("shape", ["--url", f"http://127.0.0.1:{server.server_port}/shape"], 2),
        ("partial", ["--url", f"http://127.0.0.1:{server.server_port}/partial"], 2),
        ("no-sources", ["--no-sources"], 2),
    )
    outcomes = {0: "PASS", 1: "FAIL", 2: "UNKNOWN"}
    checks = []
    try:
        for name, arguments, code in cases:
            result = subprocess.run(
                [str(python), "-I", str(example), *arguments],
                cwd=consumer,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=15,
            )
            report = json.loads(result.stdout)
            expected = {
                "schema": "source-read.assessment.v1",
                "outcome": outcomes[code],
                "exit_code": code,
                "source_count": 0 if name == "no-sources" else 1,
                "unreadable_indexes": [0] if code == 2 and name != "no-sources" else [],
                "empty_input": name == "no-sources",
                "allow_empty": False,
                "unknown_applied": code == 2,
            }
            if result.returncode != code or report != expected or result.stderr:
                raise RuntimeError(f"installed reference consumer failed: {name}")
            if "synthetic-private-consumer-sentinel" in result.stdout:
                raise RuntimeError("installed reference consumer disclosed caller content")
            checks.append({"case": name, "outcome": outcomes[code], "exit_code": code})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    receipt = {
        "schema": "source-read.installed-consumer.v1",
        "status": "PASS",
        "kind": "synthetic-installed-consumer",
        "package_version": PROJECT_VERSION,
        "wheel_sha256": hashlib.sha256(read_regular_bytes(wheel, "wheel")).hexdigest(),
        "core_sha256": hashlib.sha256((ROOT / "src/source_read/core.py").read_bytes()).hexdigest(),
        "consumer_sha256": hashlib.sha256(read_regular_bytes(example, "consumer")).hexdigest(),
        "isolated_import_within_installed_runtime": True,
        "runtime_and_consumer_outside_checkout": True,
        "checks": checks,
        "external_user_adoption": "not observed",
    }
    receipt_bytes = (json.dumps(receipt, indent=2) + "\n").encode()

    def verify_receipt(path: Path) -> None:
        if read_regular_bytes(path, "consumer receipt") != receipt_bytes:
            raise RuntimeError("consumer receipt differs from observed result")

    publish_bytes(
        ROOT,
        ROOT / "dist/installed-consumer-acceptance.json",
        receipt_bytes,
        verify_receipt,
    )
    print(f"installed reference consumer: PASS ({len(checks)} synthetic cases)")


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
        with tempfile.TemporaryDirectory(
            prefix="source-read-installed-", dir=ROOT.parent
        ) as independent:
            _verify_runtime_install(Path(independent) / "runtime", built[0])
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
