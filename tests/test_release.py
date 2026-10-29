from __future__ import annotations

import errno
import os
import stat
import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import verify_release  # noqa: E402


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
        with zipfile.ZipFile(
            path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as bundle:
            public_files = list(verify_release.PUBLIC_FILES)
            if reverse:
                public_files.reverse()
            for index, relative in enumerate(public_files):
                info = zipfile.ZipInfo(
                    f"{verify_release.ARCHIVE_PREFIX}/{relative}",
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o120777 if symlink and index == 0 else 0o100644
                info.external_attr = mode << 16
                bundle.writestr(info, (ROOT / relative).read_bytes(), compresslevel=9)
                if duplicate and index == 0:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        bundle.writestr(
                            info,
                            (ROOT / relative).read_bytes(),
                            compresslevel=9,
                        )

    def _public_tree(self, root: Path) -> None:
        for relative in verify_release.PUBLIC_FILES:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((ROOT / relative).read_bytes())

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
                    'version = "0.1.0"', 'version = "9.9.9"'
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                verify_release.VerificationError, "unexpected package identity"
            ):
                verify_release.verify_tree(public_root)

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
            )
            with mock.patch.object(
                verify_release.os, "fstat", return_value=changed_stat
            ):
                with self.assertRaisesRegex(
                    verify_release.VerificationError, "changed while reading"
                ):
                    verify_release.read_public_bytes(public_root, "README.md")

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
