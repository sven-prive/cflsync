# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for state-backed workarea operations."""

import unittest

from cflsync import StateError, Workarea
from tests.support import example_page_state, temporary_workarea


class TestWorkareaPageStates(unittest.TestCase):
    def test_enumerates_all_valid_states_by_numeric_page_id(self) -> None:
        with temporary_workarea() as workarea:
            first = example_page_state("9", directory="First page")
            second = example_page_state("10", directory="Second page")
            second.save(workarea)
            first.save(workarea)

            states = workarea.page_states()

            self.assertEqual(list(states), ["9", "10"])
            self.assertEqual(states, {"9": first, "10": second})

    def test_rejects_a_malformed_cache_entry(self) -> None:
        with temporary_workarea() as workarea:
            (workarea.cache_dir / "not-a-page.json").write_text(
                "{}", encoding="utf-8"
            )

            with self.assertRaises(StateError):
                workarea.page_states()


class TestWorkareaPageDirectory(unittest.TestCase):
    def test_returns_an_existing_managed_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            (page_directory / "page.md").write_text("# Example page\n", encoding="utf-8")

            self.assertEqual(workarea.page_directory(state), page_directory)

    def test_rejects_a_missing_page_directory_or_page_file(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()

            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)

            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)


class TestWorkareaSafePaths(unittest.TestCase):
    def test_rejects_a_traversal_page_directory(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state(directory="../outside")

            with self.assertRaises(Workarea.Error):
                workarea.page_directory(state)

    def test_rejects_a_missing_page_state(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaises(StateError):
                workarea.page_state("123456")

    def test_rejects_duplicate_page_directory_assignments(self) -> None:
        with temporary_workarea() as workarea:
            first = example_page_state("123456", directory="Shared page")
            second = example_page_state("234567", directory="Shared page")
            first.save(workarea)
            second.save(workarea)

            with self.assertRaises(Workarea.Error):
                workarea.page_states()

    def test_rejects_an_existing_title_directory_before_mutation(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            target = workarea.root_dir / state.page.directory
            target.mkdir()
            before = sorted(workarea.root_dir.iterdir())

            with self.assertRaises(Workarea.Error):
                workarea.page_directory_target(state)

            self.assertEqual(sorted(workarea.root_dir.iterdir()), before)
