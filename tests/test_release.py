from __future__ import annotations

import base64
import contextlib
import errno
import hashlib
import io
import os
import stat
import subprocess
import sys
import tempfile
import unittest
import warnings
import zipfile
import zlib
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_release  # noqa: E402
import build_source_archive  # noqa: E402
import verify_package_build  # noqa: E402


class ReleaseVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # ``dist`` is intentionally ignored by the tree verifier and is not
        # shipped in a fresh source archive. Create it before mutation tests
        # so the suite also works from a clean unpack in managed runtimes whose
        # process-wide temporary directory is not writable.
        (ROOT / "dist").mkdir(exist_ok=True)

    def _archive(
        self,
        path: Path,
        *,
        duplicate: bool = False,
        reverse: bool = False,
        symlink: bool = False,
    ) -> None:
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as bundle:
            public_files = list(verify_release.PUBLIC_FILES)
            if reverse:
                public_files.reverse()
            for index, relative in enumerate(public_files):
                info = zipfile.ZipInfo(
                    f"{verify_release.ARCHIVE_PREFIX}/{relative}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3
                mode = 0o120777 if symlink and index == 0 else 0o100644
                info.external_attr = mode << 16
                bundle.writestr(info, (ROOT / relative).read_bytes())
                if duplicate and index == 0:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        bundle.writestr(
                            info,
                            (ROOT / relative).read_bytes(),
                        )

    def _public_tree(self, root: Path) -> None:
        for relative in verify_release.PUBLIC_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())

    def _wheel(
        self,
        path: Path,
        *,
        duplicate_member: bool = False,
        duplicate_record: bool = False,
        extra_metadata_header: str | None = None,
        metadata_name: str = "source-read",
        metadata_prefix: str = "",
    ) -> None:
        prefix = f"source_read-{verify_release.PROJECT_VERSION}.dist-info/"
        record_name = f"{prefix}RECORD"
        metadata_headers = (
            "Metadata-Version: 2.5\n"
            f"Name: {metadata_name}\n"
            f"Version: {verify_release.PROJECT_VERSION}\n"
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
            "Description-Content-Type: text/markdown\n"
        )
        if extra_metadata_header is not None:
            metadata_headers += f"{extra_metadata_header}\n"
        metadata = (metadata_prefix + metadata_headers + "\n").encode() + (
            ROOT / "README.md"
        ).read_bytes()
        payloads = {
            "source_read/__init__.py": (
                ROOT / "src" / "source_read" / "__init__.py"
            ).read_bytes(),
            "source_read/core.py": (
                ROOT / "src" / "source_read" / "core.py"
            ).read_bytes(),
            f"{prefix}METADATA": metadata,
            f"{prefix}WHEEL": (
                b"Wheel-Version: 1.0\n"
                b"Generator: hatchling 1.32.3\n"
                b"Root-Is-Purelib: true\n"
                b"Tag: py3-none-any\n"
            ),
            f"{prefix}licenses/LICENSE": (ROOT / "LICENSE").read_bytes(),
        }
        rows: list[str] = []
        for name, payload in payloads.items():
            digest = base64.urlsafe_b64encode(
                hashlib.sha256(payload).digest()
            ).rstrip(b"=").decode("ascii")
            rows.append(f"{name},sha256={digest},{len(payload)}")
        if duplicate_record:
            rows.append(rows[0])
        rows.append(f"{record_name},,")
        payloads[record_name] = ("\n".join(rows) + "\n").encode()

        with zipfile.ZipFile(path, "w") as bundle:
            for name, payload in payloads.items():
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0 if os.name == "nt" else 3
                info.create_version = 20
                info.extract_version = 20
                info.external_attr = (
                    0x81A40000 if name.startswith("source_read/") else 0x01A40000
                )
                bundle.writestr(info, payload)
            if duplicate_member:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    bundle.writestr(
                        "source_read/core.py", payloads["source_read/core.py"]
                    )

    def test_public_only_tree_is_valid(self) -> None:
        with mock.patch.object(
            verify_release,
            "_files_under",
            return_value=set(verify_release.PUBLIC_FILES),
        ):
            verify_release.verify_tree(ROOT)

    def test_internal_tree_with_private_provenance_is_valid(self) -> None:
        files = set(verify_release.PUBLIC_FILES) | set(
            verify_release.LOCAL_ONLY_FILES
        )
        with mock.patch.object(verify_release, "_files_under", return_value=files):
            verify_release.verify_tree(ROOT)

    def test_current_tree_is_valid(self) -> None:
        verify_release.verify_tree(ROOT)

    def test_parent_directory_name_does_not_hide_source_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            nested_root = Path(temporary) / "source"
            nested_root.mkdir()
            (nested_root / "example.txt").write_text("synthetic", encoding="utf-8")

            self.assertEqual(verify_release._files_under(nested_root), {"example.txt"})

    def test_ignored_directory_contents_are_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            nested_root = Path(temporary) / "source"
            ignored = nested_root / "dist"
            ignored.mkdir(parents=True)
            (nested_root / "example.txt").write_text("synthetic", encoding="utf-8")
            (ignored / "ephemeral.txt").write_text("changing", encoding="utf-8")
            original_lstat = Path.lstat

            def reject_ignored_lstat(path: Path) -> os.stat_result:
                if ignored == path or ignored in path.parents:
                    raise AssertionError("ignored directory was scanned")
                return original_lstat(path)

            with mock.patch.object(Path, "lstat", reject_ignored_lstat):
                self.assertEqual(
                    verify_release._files_under(nested_root), {"example.txt"}
                )

    @unittest.skipUnless(os.name == "nt", "Windows path-case integration test")
    def test_ignored_directory_case_alias_is_not_scanned(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            nested_root = Path(temporary) / "source"
            ignored = nested_root / "DIST"
            ignored.mkdir(parents=True)
            (nested_root / "example.txt").write_text("synthetic", encoding="utf-8")
            (ignored / "ephemeral.txt").write_text("changing", encoding="utf-8")

            self.assertEqual(
                verify_release._files_under(nested_root), {"example.txt"}
            )

    def test_unreviewed_tree_file_is_rejected(self) -> None:
        files = set(verify_release.PUBLIC_FILES) | {"unexpected.txt"}
        with mock.patch.object(verify_release, "_files_under", return_value=files):
            with self.assertRaisesRegex(
                verify_release.VerificationError, "unexpected.txt"
            ):
                verify_release.verify_tree(ROOT)

    def test_tampered_license_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            (public_root / "LICENSE").write_text(
                "MIT License\n\nsubstituted\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "reviewed Nymrel MIT notice"
            ):
                verify_release.verify_tree(public_root)

    def test_version_substitution_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            metadata_path = public_root / "pyproject.toml"
            metadata_path.write_text(
                metadata_path.read_text(encoding="utf-8").replace(
                    'version = "0.2.0"', 'version = "9.9.9"'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "unexpected package identity"
            ):
                verify_release.verify_tree(public_root)

    def test_missing_locked_build_step_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            workflow_path = public_root / ".github" / "workflows" / "ci.yml"
            workflow_path.write_bytes(
                workflow_path.read_bytes().replace(
                    b"        run: python scripts/verify_package_build.py\n", b""
                )
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError,
                r"workflow requirement missing: python scripts/verify_package_build.py",
            ):
                verify_release.verify_tree(public_root)

    def test_missing_synthetic_test_step_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            workflow_path = public_root / ".github" / "workflows" / "ci.yml"
            workflow_path.write_bytes(
                workflow_path.read_bytes().replace(
                    b"        run: python -m unittest discover -s tests -v\n", b""
                )
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError,
                "workflow requirement missing: python -m unittest",
            ):
                verify_release.verify_tree(public_root)

    def test_missing_supported_matrix_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            workflow_path = public_root / ".github" / "workflows" / "ci.yml"
            workflow_path.write_bytes(
                workflow_path.read_bytes().replace(
                    b'          - os: windows-2022\n            python-version: "3.11.9"\n',
                    b"",
                )
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError,
                "workflow requirement missing: - os: windows-2022",
            ):
                verify_release.verify_tree(public_root)

    def test_missing_build_lock_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            lock = public_root / "requirements" / "build.lock"
            lock.write_text(
                "\n".join(
                    line
                    for line in lock.read_text(encoding="utf-8").splitlines()
                    if not line.startswith("tomlkit==")
                )
                + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "reviewed dependency closure"
            ):
                verify_release.verify_tree(public_root)

    def test_missing_forced_locked_tool_install_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            builder = public_root / "scripts" / "verify_package_build.py"
            builder.write_bytes(
                builder.read_bytes().replace(b'            "--force-reinstall",\n', b"")
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError,
                "locked package-build requirement missing",
            ):
                verify_release.verify_tree(public_root)

    def test_archive_cli_preserves_supplied_path_for_link_checks(self) -> None:
        supplied = Path("synthetic-junction") / "archive.zip"
        with mock.patch.object(verify_release, "verify_tree"), mock.patch.object(
            verify_release, "verify_archive"
        ) as verify_archive:
            self.assertEqual(verify_release.main(["--archive", str(supplied)]), 0)
        verify_archive.assert_called_once_with(ROOT, supplied)

    def test_archive_cli_rejects_symlink_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            target = temporary_root / "canonical.zip"
            linked = temporary_root / "linked.zip"
            target.write_bytes(verify_release.canonical_archive_bytes(ROOT))
            try:
                linked.symlink_to(target)
            except NotImplementedError as exc:
                self.skipTest(f"archive symlinks are unavailable: {exc}")
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EPERM} or getattr(
                    exc, "winerror", None
                ) == 1314:
                    self.skipTest(f"archive symlinks are unavailable: {exc}")
                raise

            errors = io.StringIO()
            with contextlib.redirect_stderr(errors):
                self.assertEqual(
                    verify_release.main(["--archive", str(linked)]), 1
                )
            self.assertIn("path contains a link", errors.getvalue())

    @unittest.skipUnless(os.name == "nt", "Windows junction integration test")
    def test_archive_cli_rejects_real_windows_junction_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            target = temporary_root / "archive-target"
            target.mkdir()
            archive = target / "canonical.zip"
            archive.write_bytes(verify_release.canonical_archive_bytes(ROOT))
            junction = temporary_root / "archive-junction"
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors):
                    self.assertEqual(
                        verify_release.main(
                            ["--archive", str(junction / archive.name)]
                        ),
                        1,
                    )
                self.assertIn("path contains a link", errors.getvalue())
            finally:
                os.rmdir(junction)
            self.assertTrue(archive.is_file())

    def test_duplicate_archive_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "duplicate.zip"
            self._archive(archive, duplicate=True)
            with self.assertRaisesRegex(
                verify_release.VerificationError, "duplicate entry"
            ):
                verify_release.verify_archive(ROOT, archive)

    def test_reordered_archive_entries_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "reordered.zip"
            self._archive(archive, reverse=True)
            with self.assertRaisesRegex(
                verify_release.VerificationError, "reviewed order"
            ):
                verify_release.verify_archive(ROOT, archive)

    def test_symlink_archive_entry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "symlink.zip"
            self._archive(archive, symlink=True)
            with self.assertRaisesRegex(
                verify_release.VerificationError, "regular 0644"
            ):
                verify_release.verify_archive(ROOT, archive)

    def test_symlink_in_public_source_tree_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            excluded = temporary_root / "excluded.txt"
            excluded.write_text("synthetic excluded content", encoding="utf-8")
            linked_readme = public_root / "README.md"
            linked_readme.unlink()
            try:
                linked_readme.symlink_to(excluded)
            except NotImplementedError as exc:
                self.skipTest(f"source symlinks are unavailable: {exc}")
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EPERM} or getattr(
                    exc, "winerror", None
                ) == 1314:
                    self.skipTest(f"source symlinks are unavailable: {exc}")
                raise

            with self.assertRaisesRegex(
                verify_release.VerificationError, "contains a link"
            ):
                verify_release.verify_tree(public_root)
            with self.assertRaisesRegex(
                verify_release.VerificationError, "contains a link"
            ):
                verify_release.canonical_archive_bytes(public_root)

    def test_symlink_leaf_is_rejected_without_platform_link_support(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            linked_readme = public_root / "README.md"
            original_lstat = Path.lstat

            def lstat_with_link(path: Path) -> os.stat_result:
                source_stat = original_lstat(path)
                if path == linked_readme:
                    values = list(source_stat)
                    values[0] = stat.S_IFLNK | 0o777
                    return os.stat_result(values)
                return source_stat

            with mock.patch.object(Path, "lstat", lstat_with_link):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "contains a link"
                ):
                    verify_release.read_public_bytes(public_root, "README.md")

    def test_hardlinked_public_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            linked_readme = public_root / "README.md"
            external = temporary_root / "external-readme.md"
            external.write_bytes(linked_readme.read_bytes())
            linked_readme.unlink()
            os.link(external, linked_readme)

            with self.assertRaisesRegex(
                verify_release.VerificationError, "hardlinked"
            ):
                verify_release.verify_tree(public_root)

    def test_linked_public_parent_is_rejected_without_platform_link_support(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            linked_parent = public_root / "src"
            original_lstat = Path.lstat

            def lstat_with_link(path: Path) -> os.stat_result:
                source_stat = original_lstat(path)
                if path == linked_parent:
                    values = list(source_stat)
                    values[0] = stat.S_IFLNK | 0o777
                    return os.stat_result(values)
                return source_stat

            with mock.patch.object(Path, "lstat", lstat_with_link):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "contains a link"
                ):
                    verify_release.read_public_bytes(
                        public_root, "src/source_read/core.py"
                    )

    def test_windows_reparse_attribute_is_rejected(self) -> None:
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        source_stat = SimpleNamespace(st_file_attributes=reparse_flag)
        with mock.patch.object(
            verify_release.stat,
            "FILE_ATTRIBUTE_REPARSE_POINT",
            reparse_flag,
            create=True,
        ):
            self.assertTrue(verify_release._is_windows_reparse_point(source_stat))

    def test_reader_rejects_windows_reparse_attribute(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary)
            source = public_root / "README.md"
            source.write_text("synthetic", encoding="utf-8")
            original_lstat = Path.lstat
            reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)

            def lstat_with_reparse(path: Path) -> object:
                source_stat = original_lstat(path)
                if path == source:
                    return SimpleNamespace(
                        st_mode=source_stat.st_mode,
                        st_dev=source_stat.st_dev,
                        st_ino=source_stat.st_ino,
                        st_nlink=1,
                        st_file_attributes=reparse_flag,
                    )
                return source_stat

            with mock.patch.object(
                verify_release.stat,
                "FILE_ATTRIBUTE_REPARSE_POINT",
                reparse_flag,
                create=True,
            ), mock.patch.object(Path, "lstat", lstat_with_reparse):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "contains a link"
                ):
                    verify_release.read_public_bytes(public_root, "README.md")

    def test_opened_file_identity_change_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary)
            source = public_root / "README.md"
            source.write_text("synthetic", encoding="utf-8")
            source_stat = source.stat()
            changed_stat = SimpleNamespace(
                st_mode=stat.S_IFREG | 0o644,
                st_dev=source_stat.st_dev,
                st_ino=source_stat.st_ino + 1,
                st_nlink=1,
            )
            with mock.patch.object(
                verify_release.os, "fstat", return_value=changed_stat
            ):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "changed while reading"
                ):
                    verify_release.read_public_bytes(public_root, "README.md")

    def test_archive_hardlink_is_rejected_before_reading(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            original = temporary_root / "original.zip"
            linked = temporary_root / "linked.zip"
            original.write_bytes(verify_release.canonical_archive_bytes(ROOT))
            os.link(original, linked)

            with self.assertRaisesRegex(
                verify_release.VerificationError, "hardlinked"
            ):
                verify_release.verify_archive(ROOT, linked)

    def test_builder_rejects_hardlinked_output_without_touching_victim(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            output = public_root / "dist" / "archive.zip"
            output.parent.mkdir()
            victim = temporary_root / "victim.txt"
            sentinel = b"synthetic victim must survive"
            victim.write_bytes(sentinel)
            os.link(victim, output)

            with self.assertRaisesRegex(
                verify_release.VerificationError, "output path is hardlinked"
            ):
                build_source_archive.build(public_root, output)
            self.assertEqual(victim.read_bytes(), sentinel)
            self.assertEqual(output.read_bytes(), sentinel)

    def test_builder_rejects_symlinked_output_without_touching_victim(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            output = public_root / "dist" / "archive.zip"
            output.parent.mkdir()
            victim = temporary_root / "victim.txt"
            sentinel = b"synthetic symlink victim must survive"
            victim.write_bytes(sentinel)
            try:
                output.symlink_to(victim)
            except NotImplementedError as exc:
                self.skipTest(f"output symlinks are unavailable: {exc}")
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EPERM} or getattr(
                    exc, "winerror", None
                ) == 1314:
                    self.skipTest(f"output symlinks are unavailable: {exc}")
                raise

            with self.assertRaisesRegex(
                verify_release.VerificationError, "output path is a link"
            ):
                build_source_archive.build(public_root, output)
            self.assertEqual(victim.read_bytes(), sentinel)

    def test_builder_atomically_replaces_existing_regular_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output = public_root / "dist" / "archive.zip"
            output.parent.mkdir()
            output.write_bytes(b"old archive")

            digest = build_source_archive.build(public_root, output)

            verify_release.verify_archive(public_root, output)
            self.assertEqual(digest, hashlib.sha256(output.read_bytes()).hexdigest())

    def test_builder_rejects_source_file_as_output_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output = public_root / "README.md"
            original = output.read_bytes()

            with self.assertRaisesRegex(
                verify_release.VerificationError, "must be under dist"
            ):
                build_source_archive.build(public_root, output)
            self.assertEqual(output.read_bytes(), original)

    @unittest.skipUnless(os.name == "nt", "Windows extended-path integration test")
    def test_builder_rejects_extended_path_alias_to_source_file(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            source = public_root / "README.md"
            original = source.read_bytes()
            extended = Path("\\\\?\\" + str(source))

            with self.assertRaisesRegex(
                verify_release.VerificationError, "aliases the source tree"
            ):
                build_source_archive.build(public_root, extended)
            self.assertEqual(source.read_bytes(), original)

    def test_builder_allows_absolute_external_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            output = temporary_root / "external" / "archive.zip"

            build_source_archive.build(public_root, output)

            verify_release.verify_archive(public_root, output)

    @unittest.skipUnless(os.name == "nt", "Windows path-case integration test")
    def test_builder_allows_case_alias_of_dist(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output = public_root / "DIST" / "archive.zip"

            build_source_archive.build(public_root, output)

            verify_release.verify_archive(public_root, output)

    @unittest.skipUnless(os.name == "nt", "Windows extended-path integration test")
    def test_builder_rebuilds_through_extended_case_alias_of_dist(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output = Path(
                "\\\\?\\" + str(public_root / "DIST" / "archive.zip")
            )

            first = build_source_archive.build(public_root, output)
            second = build_source_archive.build(public_root, output)

            self.assertEqual(first, second)
            verify_release.verify_tree(public_root)
            verify_release.verify_archive(public_root, output)

    def test_archive_cli_normalizes_case_alias_for_reproducibility(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(public_root / "scripts" / "build_source_archive.py"),
                    "--output",
                    "DIST/archive.zip",
                    "--check-reproducible",
                ],
                cwd=public_root,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            verify_release.verify_archive(
                public_root, public_root / "dist" / "archive.zip"
            )

    def test_wheel_verifier_accepts_one_consistent_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel)

            with mock.patch.object(
                verify_package_build,
                "read_regular_bytes",
                wraps=verify_package_build.read_regular_bytes,
            ) as reader:
                verify_package_build._verify_wheel_metadata(wheel)

            reader.assert_called_once_with(wheel, "wheel")

    def test_wheel_verifier_rejects_duplicate_archive_member(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel, duplicate_member=True)

            with self.assertRaisesRegex(RuntimeError, "duplicate entries"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_duplicate_record_row(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel, duplicate_record=True)

            with self.assertRaisesRegex(RuntimeError, "duplicate rows"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_metadata_substitution(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel, metadata_name="substituted")

            with self.assertRaisesRegex(RuntimeError, "canonical bytes"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_extra_metadata_header(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel, extra_metadata_header="Provides-Extra: injected")

            with self.assertRaisesRegex(RuntimeError, "canonical bytes"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_unix_from_metadata_prefix(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel, metadata_prefix="From injected-unreviewed\n")

            with self.assertRaisesRegex(RuntimeError, "canonical bytes"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_trailing_bytes(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel)
            with wheel.open("ab") as bundle:
                bundle.write(b"unreviewed trailing bytes")

            with self.assertRaisesRegex(RuntimeError, "ZIP envelope mismatch"):
                verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_nonzero_central_reserved_fields(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            wheel = Path(temporary) / "source_read-0.2.0-py3-none-any.whl"
            self._wheel(wheel)
            original = wheel.read_bytes()
            central = original.find(b"PK\x01\x02")
            self.assertGreaterEqual(central, 0)

            for offset in (7, 34):
                with self.subTest(offset=offset):
                    mutated = bytearray(original)
                    mutated[central + offset] = 1
                    wheel.write_bytes(mutated)
                    with self.assertRaisesRegex(
                        RuntimeError, "member metadata mismatch"
                    ):
                        verify_package_build._verify_wheel_metadata(wheel)

    def test_wheel_verifier_rejects_bytes_after_deflate_eof(self) -> None:
        payload = b"synthetic reviewed member"
        compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
        compressed = compressor.compress(payload) + compressor.flush()
        info = SimpleNamespace(
            filename="synthetic.txt",
            file_size=len(payload),
            CRC=zlib.crc32(payload) & 0xFFFFFFFF,
        )

        verify_package_build._verify_deflate_member(compressed, info)
        with self.assertRaisesRegex(RuntimeError, "compressed payload mismatch"):
            verify_package_build._verify_deflate_member(
                compressed + b"unreviewed compressed extent", info
            )

    def test_builder_rejects_reparse_output_parent(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output_parent = public_root / "dist"
            output_parent.mkdir()
            output = output_parent / "archive.zip"
            original_lstat = Path.lstat

            def lstat_with_reparse(path: Path) -> object:
                source_stat = original_lstat(path)
                if path == output_parent:
                    return SimpleNamespace(
                        st_mode=source_stat.st_mode,
                        st_dev=source_stat.st_dev,
                        st_ino=source_stat.st_ino,
                        force_reparse=True,
                    )
                return source_stat

            with mock.patch.object(
                build_source_archive,
                "_is_windows_reparse_point",
                side_effect=lambda source_stat: getattr(
                    source_stat, "force_reparse", False
                ),
            ), mock.patch.object(Path, "lstat", lstat_with_reparse):
                with self.assertRaisesRegex(
                    verify_release.VerificationError,
                    "output directory contains a link",
                ):
                    build_source_archive.build(public_root, output)

    @unittest.skipUnless(os.name == "nt", "Windows junction integration test")
    def test_builder_rejects_real_windows_junction_output_parent(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            temporary_root = Path(temporary)
            public_root = temporary_root / "source"
            self._public_tree(public_root)
            target = temporary_root / "external-output"
            target.mkdir()
            junction = public_root / "dist"
            created = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(junction), str(target)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr or created.stdout)
            try:
                junction_stat = junction.lstat()
                self.assertTrue(
                    verify_release._is_windows_reparse_point(junction_stat)
                )
                with self.assertRaisesRegex(
                    verify_release.VerificationError,
                    "output directory contains a link",
                ):
                    build_source_archive.build(public_root, junction / "archive.zip")
                self.assertEqual(tuple(target.iterdir()), ())
            finally:
                os.rmdir(junction)
            self.assertTrue(target.is_dir())

    def test_builder_preserves_prior_output_when_staged_verification_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            public_root = Path(temporary) / "source"
            self._public_tree(public_root)
            output = public_root / "dist" / "archive.zip"
            output.parent.mkdir()
            original = b"previous reviewed archive"
            output.write_bytes(original)

            with mock.patch.object(
                build_source_archive,
                "verify_archive",
                side_effect=verify_release.VerificationError("synthetic failure"),
            ):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "synthetic failure"
                ):
                    build_source_archive.build(public_root, output)

            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(tuple(output.parent.glob(".archive.zip.*.tmp")), ())

    def test_appended_archive_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "appended.zip"
            archive.write_bytes(
                verify_release.canonical_archive_bytes(ROOT) + b"unreviewed payload"
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "canonical deterministic build"
            ):
                verify_release.verify_archive(ROOT, archive)

    def test_prepended_archive_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "prepended.zip"
            archive.write_bytes(
                b"unreviewed payload" + verify_release.canonical_archive_bytes(ROOT)
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "canonical deterministic build"
            ):
                verify_release.verify_archive(ROOT, archive)

    def test_local_header_only_extra_bytes_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT / "dist") as temporary:
            archive = Path(temporary) / "local-extra.zip"
            archive.write_bytes(verify_release.canonical_archive_bytes(ROOT))
            with zipfile.ZipFile(archive) as bundle:
                local_offset = bundle.infolist()[-1].header_offset

            payload = b"unreviewed-local-extra"
            raw = bytearray(archive.read_bytes())
            name_length = int.from_bytes(
                raw[local_offset + 26 : local_offset + 28], "little"
            )
            extra_length = int.from_bytes(
                raw[local_offset + 28 : local_offset + 30], "little"
            )
            insertion = local_offset + 30 + name_length + extra_length
            raw[insertion:insertion] = payload
            raw[local_offset + 28 : local_offset + 30] = len(payload).to_bytes(
                2, "little"
            )
            end_record = raw.rfind(b"PK\x05\x06")
            central_offset = int.from_bytes(raw[end_record + 16 : end_record + 20], "little")
            raw[end_record + 16 : end_record + 20] = (
                central_offset + len(payload)
            ).to_bytes(4, "little")
            archive.write_bytes(raw)

            with self.assertRaisesRegex(
                verify_release.VerificationError, "canonical deterministic build"
            ):
                verify_release.verify_archive(ROOT, archive)


if __name__ == "__main__":
    unittest.main()
