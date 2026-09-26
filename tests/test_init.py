# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Workarea initialization anchored at a root page, and refusal of version-1 workareas."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import Profile, Workarea
from cflsync.cli import main
from tests.support import FakeConfluence


class TestInitCommand(unittest.TestCase):

    def setUp(self):
        self.site = FakeConfluence()
        self.site.add_page("100", "Space home")
        self.site.add_page("123456", "Root page", parent_id="100")
        self.site.add_page("200", "Duplicate", parent_id="100")
        self.site.add_page("300", "Duplicate", parent_id="100")
        self.profiles = {"default": Profile("example.atlassian.net", "user", "token")}

    def _run(self, root, arguments):
        output = StringIO()
        errors = StringIO()
        with patch("cflsync.cli.Path.cwd", return_value=root):
            with patch("cflsync.cli.Config.find", return_value=SimpleNamespace(profiles=self.profiles)):
                with patch("cflsync.cli.APIClient", return_value=self.site.client()):
                    with redirect_stdout(output), redirect_stderr(errors):
                        status = main(["cflsync", *arguments])

        return status, output.getvalue(), errors.getvalue()

    def test_anchors_a_new_workarea_at_a_page_id(self) -> None:
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)

            status, output, errors = self._run(root, ["init", "123456"])

            self.assertEqual((status, errors), (0, ""))
            self.assertIn("anchored at page '123456' (Root page), using profile 'default'", output)
            self.assertIn("cflsync page pull 123456", output)
            workarea = Workarea.find(root)
            self.assertEqual((workarea.root_page_id, workarea.profile), ("123456", "default"))
            self.assertEqual(list(workarea.cache_dir.iterdir()), [])

    def test_anchors_a_new_workarea_at_a_page_title_with_a_named_profile(self) -> None:
        self.profiles["work"] = self.profiles.pop("default")
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)

            status, _, errors = self._run(root, ["init", "-p", "work", "Root page"])

            self.assertEqual((status, errors), (0, ""))
            workarea = Workarea.find(root)
            self.assertEqual((workarea.root_page_id, workarea.profile), ("123456", "work"))

    def test_failures_leave_no_workarea(self) -> None:
        cases = [
            (["init", "Duplicate"], "multiple pages match title 'Duplicate': 200, 300"),
            (["init", "No such page"], "no page matches title 'No such page'"),
            (["init", "999999"], "Confluence resource was not found"),
            (["init", "-p", "missing", "123456"], "credential profile 'missing' does not exist"), ]
        for arguments, error in cases:
            with self.subTest(arguments=arguments):
                with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
                    root = Path(temporary_dir)

                    status, _, errors = self._run(root, arguments)

                    self.assertEqual(status, 1)
                    self.assertIn(error, errors)
                    self.assertEqual(list(root.iterdir()), [])

    def test_refuses_an_existing_workarea(self) -> None:
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)
            Workarea.init(root, "100")

            status, _, errors = self._run(root, ["init", "123456"])

            self.assertEqual(status, 1)
            self.assertIn("already part of a cflsync workarea", errors)
            self.assertEqual(Workarea.find(root).root_page_id, "100")


class TestVersion1Workarea(unittest.TestCase):

    def test_every_workarea_command_refuses_a_version_1_workarea(self) -> None:
        commands = [
            ["page", "create", "123456", "New page"], ["page", "pull", "123456"], ["page", "push", "123456"],
            ["page", "status", "123456"], ["page", "rename", "123456", "Renamed"], ["page", "move", "123456", "456789"],
            ["page", "remove", "--force", "123456"], ]
        with TemporaryDirectory(prefix="cflsync-init-") as temporary_dir:
            root = Path(temporary_dir)
            Workarea.init(root, "123456")
            (root / ".cflsync" / "root").unlink()
            for arguments in commands:
                with self.subTest(command=arguments[1]):
                    errors = StringIO()
                    with patch("cflsync.cli.Path.cwd", return_value=root):
                        with patch("cflsync.cli.Config.find", side_effect=AssertionError("credentials must not be read")):
                            with redirect_stderr(errors):
                                status = main(["cflsync", *arguments])

                    self.assertEqual(status, 1)
                    self.assertIn("is a version-1 cflsync workarea", errors.getvalue())
                    self.assertIn("cflsync init ROOT_PAGE_REF", errors.getvalue())


# vim: set ts=4 sw=4 et tw=132:
