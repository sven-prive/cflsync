# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for the self-contained zipapp distribution artifact."""

import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_ROOT / "packaging" / "scripts" / "build_zipapp.py"
ZIPAPP = PROJECT_ROOT / "dist" / "cflsync.pyz"
SCOOP_LAUNCHER = PROJECT_ROOT / "packaging" / "scoop" / "cflsync.cmd"


class TestZipapp(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run([sys.executable, str(BUILD_SCRIPT)], cwd=PROJECT_ROOT, capture_output=True, check=True, text=True)

    def test_vendors_platformdirs(self) -> None:
        with zipfile.ZipFile(ZIPAPP) as archive:
            self.assertIn("platformdirs/__init__.py", archive.namelist())

    def test_scoop_launcher_forwards_arguments_and_exit_status(self) -> None:
        self.assertEqual(SCOOP_LAUNCHER.read_text(encoding="utf-8"), '@python "%~dp0cflsync.pyz" %*\n@exit /b %errorlevel%\n', )

    def test_help_is_identical_across_entry_points(self) -> None:
        executable_name = "cflsync.exe" if sys.platform == "win32" else "cflsync"
        console_script = Path(sys.executable).with_name(executable_name)
        console = subprocess.run([console_script, "--help"], text=True, capture_output=True, check=False)
        module = subprocess.run(
            [sys.executable, "-m", "cflsync", "--help"], cwd=PROJECT_ROOT, text=True, capture_output=True, check=False,
        )
        with tempfile.TemporaryDirectory() as working_directory:
            zipapp = subprocess.run(
                [sys.executable, "-S", str(ZIPAPP), "--help"], cwd=working_directory, text=True, capture_output=True, check=False,
            )

        self.assertEqual(console.returncode, 0, console.stderr)
        self.assertEqual(module.returncode, 0, module.stderr)
        self.assertEqual(zipapp.returncode, 0, zipapp.stderr)
        self.assertEqual(module.stdout, console.stdout)
        self.assertEqual(zipapp.stdout, console.stdout)


# vim: set ts=4 sw=4 et tw=132:
