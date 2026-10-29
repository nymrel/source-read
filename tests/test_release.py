from __future__ import annotations

import sys
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
