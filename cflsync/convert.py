# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Document markup conversion boundary."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping

from .errors import SyncError


class PandocError(SyncError):
    """Raised when Pandoc cannot perform a required conversion."""


class PandocRunner:
    """Run a compatible Pandoc executable with GFM and native JSON."""

    API_VERSION = (1, 23, 1, 2)

    def __init__(self, binary: str = "pandoc", run=None) -> None:
        self.binary = binary
        self._run_process = run or subprocess.run
        self.version = self._discover_version()
        self._validate_api_version()

    def gfm_to_ast(self, gfm: str) -> dict[str, object]:
        """Parse GFM to a validated Pandoc native JSON AST."""
        return self._ast(self._run(["--from=gfm", "--to=json"], gfm))

    def ast_to_gfm(self, ast: Mapping[str, object]) -> str:
        """Render a validated Pandoc native JSON AST to canonical GFM."""
        self._validate_ast(ast)
        return self._run(["--from=json", "--to=gfm", "--wrap=none"], json.dumps(ast, ensure_ascii=False, separators=(",", ":")))

    def _discover_version(self) -> str:
        output = self._run(["--version"], "")
        match = re.match(r"pandoc (\S+)", output)
        if match is None:
            raise PandocError(f"cannot determine Pandoc version from '{self.binary}'")

        return match.group(1)

    def _validate_api_version(self) -> None:
        ast = self._ast(self._run(["--from=gfm", "--to=json"], ""))
        self._validate_ast(ast)

    def _ast(self, text: str) -> dict[str, object]:
        try:
            ast = json.loads(text)
        except json.JSONDecodeError as error:
            raise PandocError("Pandoc did not produce valid native JSON") from error
        if not isinstance(ast, dict):
            raise PandocError("Pandoc native JSON must be an object")

        self._validate_ast(ast)
        return ast

    def _validate_ast(self, ast: Mapping[str, object]) -> None:
        version = ast.get("pandoc-api-version")
        if not isinstance(version, list) or any(type(part) is not int for part in version):
            raise PandocError("Pandoc native JSON has no valid pandoc-api-version")
        if tuple(version) != self.API_VERSION:
            expected = ".".join(map(str, self.API_VERSION))
            actual = ".".join(map(str, version))
            raise PandocError(f"Pandoc JSON API version {actual} is unsupported; expected {expected}")
        if not isinstance(ast.get("meta"), Mapping):
            raise PandocError("Pandoc native JSON has no meta object")
        if not isinstance(ast.get("blocks"), list):
            raise PandocError("Pandoc native JSON has no blocks list")

    def _run(self, arguments: list[str], input_text: str) -> str:
        command = [self.binary, *arguments]
        try:
            result = self._run_process(command, input=input_text, text=True, capture_output=True)
        except FileNotFoundError as error:
            raise PandocError(f"Pandoc executable '{self.binary}' was not found") from error
        except OSError as error:
            raise PandocError(f"cannot run Pandoc executable '{self.binary}': {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise PandocError(f"Pandoc failed: {detail}")
        if not isinstance(result.stdout, str):
            raise PandocError("Pandoc produced non-text output")

        return result.stdout


# vim: set ts=4 sw=4 et tw=132:
