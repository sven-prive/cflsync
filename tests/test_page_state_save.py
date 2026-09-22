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
            expected.save(workarea)

            path = workarea.cache_path(expected.page.id)
            self.assertEqual(PageState.load(workarea, expected.page.id), expected)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_failed_replacement_preserves_the_previous_complete_state(self) -> None:
        with temporary_workarea() as workarea:
            previous = example_page_state()
            previous.save(workarea)
            changed = PageState(
                page=PageMetadata(
                    id=previous.page.id,
                    title=previous.page.title,
                    directory=previous.page.directory,
                    version=18,
                    content_hash="a" * 64,
                ),
                attachments=previous.attachments,
            )

            with patch("cflsync.os.replace", side_effect=OSError("injected failure")):
                with self.assertRaises(StateError):
                    changed.save(workarea)

            self.assertEqual(PageState.load(workarea, previous.page.id), previous)
