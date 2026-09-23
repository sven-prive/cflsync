# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Smoke tests for the standard-library test runner."""

import unittest

import cflsync


class TestProjectImport(unittest.TestCase):

    def test_module_is_importable(self) -> None:
        self.assertTrue(callable(cflsync.main))


# vim: set ts=4 sw=4 et tw=132:
