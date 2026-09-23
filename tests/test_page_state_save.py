# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for atomic page-state persistence."""

import stat
import unittest
from unittest.mock import patch

from cflsync import PageMetadata, PageState, StateError
from tests.support import example_page_state, temporary_workarea


class TestPageStateSave(unittest.TestCase):

    def test_writes_a_loadable_state_file_with_mode_0600(self) -> None:
        with temporary_workarea() as workarea:
            expected = example_page_state()
            expected.save(workarea.cache_path(expected.page.id))

            path = workarea.cache_path(expected.page.id)
            self.assertEqual(PageState.load(workarea.cache_path(expected.page.id)), expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_failed_replacement_preserves_the_previous_complete_state(self) -> None:
        with temporary_workarea() as workarea:
            previous = example_page_state()
            previous.save(workarea.cache_path(previous.page.id))
            changed = PageState(
                page=PageMetadata(
                    id=previous.page.id,
                    title=previous.page.title,
                    directory=previous.page.directory,
                    version=18,
                    content_hash="a" * 64),
                attachments=previous.attachments)

            with patch("cflsync.os.replace", side_effect=OSError("injected failure")):
                with self.assertRaises(StateError):
                    changed.save(workarea.cache_path(changed.page.id))

            self.assertEqual(PageState.load(workarea.cache_path(previous.page.id)), previous)

    def test_failed_temporary_state_write_removes_temporary_file(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            path = workarea.cache_path(state.page.id)

            with patch("cflsync.workarea.os.fsync", side_effect=OSError("injected failure")):
                with self.assertRaisesRegex(StateError, "cannot write"):
                    state.save(path)

            self.assertFalse(path.exists())
            self.assertEqual(list(workarea.cache_dir.glob(".*.tmp")), [])


# vim: set ts=4 sw=4 et tw=132:
