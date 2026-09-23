# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for temporary workarea fixtures."""

from pathlib import Path
import unittest

from tests.support import temporary_workarea


class TestTemporaryWorkarea(unittest.TestCase):

    def test_creates_an_isolated_workarea_and_removes_it(self) -> None:
        with temporary_workarea("test-profile") as workarea:
            root = workarea.root_dir
            self.assertTrue(root.is_dir())
            self.assertEqual(workarea.profile, "test-profile")
            self.assertTrue((root / ".cflsync" / "cache").is_dir())
            self.assertFalse((root / ".git").exists())

        self.assertFalse(root.exists())


# vim: set ts=4 sw=4 et tw=132:
