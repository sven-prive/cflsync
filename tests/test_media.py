# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for managed attachment path resolution."""

import unittest

from cflsync import MediaResolutionError, MediaResolver


class TestMediaResolver(unittest.TestCase):

    def test_resolves_attachment_ids_and_paths(self) -> None:
        resolver = MediaResolver([("diagram.png", "987654"), ("report.xlsx", "456789")])

        self.assertEqual(resolver.path_for("987654"), "_attachments/diagram.png")
        self.assertEqual(resolver.path_for("456789"), "_attachments/report.xlsx")
        self.assertEqual(resolver.id_for("_attachments/diagram.png"), "987654")
        self.assertEqual(resolver.id_for("_attachments/report.xlsx"), "456789")

    def test_rejects_ambiguous_and_unsafe_manifest_entries(self) -> None:
        with self.assertRaisesRegex(MediaResolutionError, "filename.*ambiguous"):
            MediaResolver([("diagram.png", "987654"), ("diagram.png", "456789")])

        with self.assertRaisesRegex(MediaResolutionError, "ID.*ambiguous"):
            MediaResolver([("first.png", "987654"), ("second.png", "987654")])

        with self.assertRaisesRegex(MediaResolutionError, "unsafe"):
            MediaResolver([("../diagram.png", "987654")])

    def test_rejects_unmanaged_and_traversal_paths(self) -> None:
        resolver = MediaResolver([("diagram.png", "987654")])

        with self.assertRaisesRegex(MediaResolutionError, "not managed"):
            resolver.path_for("456789")

        with self.assertRaisesRegex(MediaResolutionError, "not managed"):
            resolver.id_for("_attachments/missing.png")

        for path in ("_attachments/../diagram.png", "../_attachments/diagram.png", "/_attachments/diagram.png"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(MediaResolutionError, "outside _attachments"):
                    resolver.id_for(path)


# vim: set ts=4 sw=4 et tw=132:
