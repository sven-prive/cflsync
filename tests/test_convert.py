# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for the document markup conversion boundary."""

import json
from subprocess import CompletedProcess
import unittest

from cflsync import PandocError, PandocRunner


def ast_json(version: tuple[int, ...] = PandocRunner.API_VERSION) -> str:
    return json.dumps({"pandoc-api-version": list(version), "meta": {}, "blocks": []})


class FakePandoc:

    def __init__(self, api_version: tuple[int, ...] = PandocRunner.API_VERSION) -> None:
        self.api_version = api_version
        self.calls: list[list[str]] = []

    def __call__(self, command, input, text, capture_output) -> CompletedProcess:
        self.calls.append(command)
        if command[1:] == ["--version"]:
            return CompletedProcess(command, 0, "pandoc 3.10\n", "")
        if command[1:] == ["--from=gfm", "--to=json"]:
            return CompletedProcess(command, 0, ast_json(self.api_version), "")
        if command[1:] == ["--from=json", "--to=gfm", "--wrap=none"]:
            return CompletedProcess(command, 0, "# Heading\n", "")
        raise AssertionError(f"unexpected Pandoc command: {command}")


class TestPandocDiscovery(unittest.TestCase):

    def test_records_the_binary_and_pinned_json_api_versions(self) -> None:
        pandoc = FakePandoc()

        runner = PandocRunner(run=pandoc)

        self.assertEqual(runner.version, "3.10")
        self.assertEqual(pandoc.calls[:2], [["pandoc", "--version"], ["pandoc", "--from=gfm", "--to=json"]])
        self.assertTrue(all(isinstance(command, list) for command in pandoc.calls))

    def test_rejects_a_missing_binary(self) -> None:

        def missing_pandoc(*args, **kwargs):
            raise FileNotFoundError()

        with self.assertRaisesRegex(PandocError, "was not found"):
            PandocRunner(binary="missing-pandoc", run=missing_pandoc)

    def test_rejects_an_unsupported_json_api_version(self) -> None:
        with self.assertRaisesRegex(PandocError, "is unsupported"):
            PandocRunner(run=FakePandoc((1, 22, 0, 0)))


class TestPandocConversion(unittest.TestCase):

    def test_converts_gfm_to_a_parseable_pandoc_document(self) -> None:
        runner = PandocRunner()

        pandoc = runner.gfm_to_pandoc("# Heading\n\nParagraph\n")

        self.assertEqual(pandoc["pandoc-api-version"], list(PandocRunner.API_VERSION))
        blocks = pandoc["blocks"]
        if not isinstance(blocks, list) or not blocks or not isinstance(blocks[0], dict):
            self.fail("Pandoc document does not contain a block object")

        self.assertEqual(blocks[0]["t"], "Header")

    def test_writes_equivalent_gfm_in_one_canonical_form(self) -> None:
        runner = PandocRunner()

        first = runner.pandoc_to_gfm(runner.gfm_to_pandoc("# Heading\n\nParagraph\n"))
        second = runner.pandoc_to_gfm(runner.gfm_to_pandoc("# Heading\n\nParagraph\n\n"))

        self.assertEqual(first, "# Heading\n\nParagraph\n")
        self.assertEqual(second, first)

    def test_reports_a_failed_conversion_as_a_domain_error(self) -> None:

        class FailingPandoc(FakePandoc):

            def __call__(self, command, input, text, capture_output) -> CompletedProcess:
                if command[1:] == ["--from=gfm", "--to=json"] and input:
                    return CompletedProcess(command, 1, "", "invalid GFM")
                return super().__call__(command, input, text, capture_output)

        runner = PandocRunner(run=FailingPandoc())

        with self.assertRaisesRegex(PandocError, "invalid GFM"):
            runner.gfm_to_pandoc("# Heading\n")


# vim: set ts=4 sw=4 et tw=132:
