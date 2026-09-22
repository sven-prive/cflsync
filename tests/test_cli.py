# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for cflsync command-line routing and expected errors."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

from cflsync import SyncError
from cflsync.cli import PageCreateCommand, PagePullCommand, PagePushCommand, PageStatusCommand, main


class TestPageCommandDispatch(unittest.TestCase):

    def test_dispatches_page_create_arguments_to_its_command(self) -> None:
        with patch.object(PageCreateCommand, "run", return_value=0) as run:
            result = main(["cflsync", "page", "create", "123456", "Example page"])

        self.assertEqual(result, 0)
        run.assert_called_once_with("123456", "Example page")

    def test_dispatches_page_reference_arguments_to_page_commands(self) -> None:
        cases = [("pull", PagePullCommand), ("push", PagePushCommand), ("status", PageStatusCommand)]
        for command_name, command_type in cases:
            with self.subTest(command=command_name):
                with patch.object(command_type, "run", return_value=0) as run:
                    result = main(["cflsync", "page", command_name, "Example page"])

                self.assertEqual(result, 0)
                run.assert_called_once_with("Example page")

    def test_page_help_lists_every_documented_page_command(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            with self.assertRaises(SystemExit) as raised:
                main(["cflsync", "page", "--help"])

        self.assertEqual(raised.exception.code, 0)
        for command_name in ["create", "pull", "push", "status"]:
            self.assertIn(command_name, output.getvalue())


class TestExpectedCommandErrors(unittest.TestCase):

    def test_reports_sync_errors_without_a_traceback(self) -> None:
        error_output = StringIO()
        with patch.object(PagePullCommand, "run", side_effect=SyncError("injected failure")):
            with redirect_stderr(error_output):
                result = main(["cflsync", "page", "pull", "123456"])

        self.assertEqual(result, 1)
        self.assertEqual(error_output.getvalue(), "cflsync: injected failure\n")


# vim: set ts=4 sw=4 et tw=132:
