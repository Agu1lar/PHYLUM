import os
import unittest
from unittest.mock import patch

import app_main


class DownloadArtifactsTests(unittest.TestCase):
    def test_latest_windows_installer_path_picks_newest(self):
        with self.subTest("newest installer is selected"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as temp_dir:
                older = app_main.Path(temp_dir) / "phylum_0.0.9_x64-setup.exe"
                newer = app_main.Path(temp_dir) / "phylum_0.1.0_x64-setup.exe"
                older.write_bytes(b"old")
                newer.write_bytes(b"new")
                os.utime(older, (1, 1))
                os.utime(newer, (2, 2))

                with patch.object(app_main, "INSTALLER_DIR", app_main.Path(temp_dir)):
                    result = app_main._latest_windows_installer_path()

                self.assertEqual(result, newer)

    def test_latest_windows_installer_path_returns_none_when_missing(self):
        with self.subTest("missing directory returns none"):
            from tempfile import TemporaryDirectory

            with TemporaryDirectory() as temp_dir:
                missing_dir = app_main.Path(temp_dir) / "missing"

                with patch.object(app_main, "INSTALLER_DIR", missing_dir):
                    result = app_main._latest_windows_installer_path()

                self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
