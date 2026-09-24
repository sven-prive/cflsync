# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Build a Windows cflsync archive with an embedded CPython runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_CONFIGURATION = PROJECT_ROOT / "packaging" / "windows-python.json"
ZIPAPP = PROJECT_ROOT / "dist" / "cflsync.pyz"
LAUNCHER = PROJECT_ROOT / "packaging" / "scoop" / "cflsync.cmd"


def _read_configuration() -> dict:
    return json.loads(RUNTIME_CONFIGURATION.read_text(encoding="utf-8"))


def _project_version() -> str:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return project["project"]["version"]


def _verify_runtime(path: Path, runtime: dict) -> None:
    with path.open("rb") as archive:
        digest = hashlib.file_digest(archive, "sha256").hexdigest()
    if digest != runtime["sha256"]:
        raise ValueError(f"embedded Python archive has SHA-256 {digest}, expected {runtime['sha256']}")


def _download_runtime(path: Path, runtime: dict) -> None:
    with urllib.request.urlopen(runtime["url"]) as response, path.open("wb") as destination:
        shutil.copyfileobj(response, destination)


def _write_archive(staging: Path, output: Path) -> None:
    temporary_output = output.with_suffix(".zip.tmp")
    temporary_output.unlink(missing_ok=True)
    with zipfile.ZipFile(temporary_output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging))
    temporary_output.replace(output)


def build(architecture: str, runtime_archive: Path | None = None, configuration: dict | None = None) -> Path:
    """Build and return the release archive for one Windows architecture."""
    configuration = configuration or _read_configuration()
    try:
        runtime = configuration["architectures"][architecture]
    except KeyError as error:
        supported = ", ".join(configuration["architectures"])
        raise ValueError(f"unsupported Windows architecture '{architecture}'; expected one of {supported}") from error

    if not ZIPAPP.is_file():
        raise FileNotFoundError(f"zipapp does not exist: {ZIPAPP}")

    output = PROJECT_ROOT / "dist" / f"cflsync-{_project_version()}-windows-{architecture}.zip"
    output.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cflsync-windows-") as temporary_directory:
        staging = Path(temporary_directory) / "archive"
        staging.mkdir()
        if runtime_archive is None:
            runtime_archive = Path(temporary_directory) / "python-embed.zip"
            _download_runtime(runtime_archive, runtime)
        _verify_runtime(runtime_archive, runtime)

        with zipfile.ZipFile(runtime_archive) as archive:
            archive.extractall(staging)
        if not (staging / "python.exe").is_file():
            raise ValueError("embedded Python archive does not contain python.exe")

        shutil.copy2(ZIPAPP, staging / "cflsync.pyz")
        shutil.copy2(LAUNCHER, staging / "cflsync.cmd")
        _write_archive(staging, output)

    print(output)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("architecture", choices=("x64", "arm64"))
    parser.add_argument("--runtime", type=Path, help="use a previously downloaded embedded Python ZIP")
    arguments = parser.parse_args()
    build(arguments.architecture, arguments.runtime)


if __name__ == "__main__":
    main()

# vim: set ts=4 sw=4 et tw=132:
