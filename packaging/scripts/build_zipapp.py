# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build the self-contained cflsync zipapp distribution artifact."""

from __future__ import annotations

import shutil
import tempfile
import zipapp
from pathlib import Path

import platformdirs
import tzdata
import tzlocal

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "dist" / "cflsync.pyz"
MAIN_MODULE = """\
import sys

from cflsync.cli import main

raise SystemExit(main([\"cflsync\", *sys.argv[1:]]))
"""


def _copy_package(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def main() -> None:
    """Write dist/cflsync.pyz with all runtime dependencies included."""
    platformdirs_root = Path(platformdirs.__file__).parent
    tzdata_root = Path(tzdata.__file__).parent
    tzlocal_root = Path(tzlocal.__file__).parent
    OUTPUT.parent.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cflsync-zipapp-") as temporary_directory:
        staging = Path(temporary_directory)
        _copy_package(PROJECT_ROOT / "cflsync", staging / "cflsync")
        _copy_package(platformdirs_root, staging / "platformdirs")
        _copy_package(tzdata_root, staging / "tzdata")
        _copy_package(tzlocal_root, staging / "tzlocal")
        (staging / "__main__.py").write_text(MAIN_MODULE, encoding="utf-8")

        temporary_output = OUTPUT.with_suffix(".pyz.tmp")
        temporary_output.unlink(missing_ok=True)
        zipapp.create_archive(staging, target=temporary_output, compressed=True)
        temporary_output.replace(OUTPUT)

    print(OUTPUT)


if __name__ == "__main__":
    main()

# vim: set ts=4 sw=4 et tw=132:
