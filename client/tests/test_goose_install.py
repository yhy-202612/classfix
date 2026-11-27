import hashlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from evetech import goose_install


class GooseInstallTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data_dir = Path(directory.name) / "settings"
        self.enterContext(patch.dict(os.environ, {"EVETECH_DATA_DIR": str(self.data_dir)}))
        files = {"GooseDesktop.exe": b"test exe", "GooseModdingAPI.dll": b"test API"}
        self.enterContext(patch.object(goose_install, "_STOCK_FILES", {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        }))
        archive = io.BytesIO()
        with ZipFile(archive, "w") as zip_file:
            for name, data in files.items():
                zip_file.writestr(f"Desktop Goose v0.31/DesktopGoose v0.31/{name}", data)
            zip_file.writestr("Desktop Goose v0.31/DesktopGoose v0.31/Assets/Sound/NotEmbedded/Honk1.mp3", b"test honk")
        self.archive = Path(directory.name) / "goose.zip"
        payload = archive.getvalue()
        self.archive.write_bytes(payload)
        self.enterContext(patch.object(goose_install, "_GOOSE_SIZE", len(payload)))
        self.enterContext(patch.object(goose_install, "_GOOSE_SHA256", hashlib.sha256(payload).hexdigest()))

    def test_install_verified_zip_and_reuse_existing_installation(self):
        folder = goose_install.install_goose(self.archive)
        self.assertEqual(folder, self.data_dir / "desktop-goose" / "v0.31")
        self.assertTrue(goose_install.original_goose_available(folder))
        with patch.object(goose_install, "download_archive") as download:
            self.assertEqual(goose_install.install_goose(), folder)
        download.assert_not_called()

    def test_reject_modified_zip_before_extraction(self):
        data = self.archive.read_bytes()
        self.archive.write_bytes(b"!" + data[1:])
        with self.assertRaisesRegex(goose_install.GooseInstallError, "not the official"):
            goose_install.install_goose(self.archive)
        self.assertFalse(self.data_dir.exists())

    def test_failure_preserves_an_existing_installation(self):
        folder = goose_install.install_goose(self.archive)
        (folder / "GooseDesktop.exe").write_bytes(b"user file")
        alternate = goose_install.install_goose(self.archive)
        self.assertNotEqual(alternate, folder)
        self.assertEqual((folder / "GooseDesktop.exe").read_bytes(), b"user file")
        self.assertTrue(goose_install.original_goose_available(alternate))

    def test_download_links_are_limited_to_itch_io(self):
        for url in ("http://samperson.itch.io/desktop-goose/purchase",
                    "https://samperson.itch.io@other.example/desktop-goose/purchase",
                    "https://samperson.itch.io/other/purchase"):
            with self.subTest(url=url), self.assertRaises(goose_install.GooseInstallError):
                goose_install._official_url(url)
        with self.assertRaises(goose_install.GooseInstallError):
            goose_install._official_url("https://example.com/goose.zip", archive=True)
