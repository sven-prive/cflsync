# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for resolving page command references."""

from collections.abc import Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from cflsync import PageRef, PageRefError
from tests.support import example_page_state, temporary_workarea


class FakeAPI:

    def __init__(self, page_ids: Sequence[str] = (), pages: Sequence[SimpleNamespace] = ()) -> None:
        self.page_ids = list(page_ids)
        self.pages = list(pages)
        self.get_page_calls: list[str] = []
        self.find_pages_by_title_calls: list[str] = []

    def get_page(self, page_id: str) -> SimpleNamespace:
        self.get_page_calls.append(page_id)
        return SimpleNamespace(id=self.page_ids.pop(0))

    def find_pages_by_title(self, title: str) -> list[SimpleNamespace]:
        self.find_pages_by_title_calls.append(title)
        return self.pages


class TestPageRefPaths(unittest.TestCase):

    def test_resolves_a_managed_page_file_without_an_api_request(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            state.save(workarea.cache_path(state.page.id))
            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            page_file = page_directory / "page.md"
            page_file.write_text("# Example page\n", encoding="utf-8")
            api = FakeAPI()

            page_ref = PageRef.resolve(page_file, workarea, api)

            self.assertEqual(page_ref.page_id, state.page.id)
            self.assertEqual(api.get_page_calls, [])
            self.assertEqual(api.find_pages_by_title_calls, [])

    def test_resolves_a_managed_page_directory_without_an_api_request(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state()
            state.save(workarea.cache_path(state.page.id))
            page_directory = workarea.root_dir / state.page.directory
            page_directory.mkdir()
            (page_directory / "page.md").touch()
            api = FakeAPI()

            page_ref = PageRef.resolve(page_directory, workarea, api)

            self.assertEqual(page_ref.page_id, state.page.id)
            self.assertEqual(api.get_page_calls, [])
            self.assertEqual(api.find_pages_by_title_calls, [])

    def test_rejects_an_unmanaged_or_outside_path_without_an_api_request(self) -> None:
        with temporary_workarea() as workarea:
            unmanaged_file = workarea.root_dir / "notes.md"
            unmanaged_file.touch()
            api = FakeAPI()

            with self.assertRaises(PageRefError):
                PageRef.resolve(unmanaged_file, workarea, api)

            with TemporaryDirectory(prefix="cflsync-page-ref-") as temporary_dir:
                outside_file = Path(temporary_dir) / "page.md"
                outside_file.touch()
                with self.assertRaises(PageRefError):
                    PageRef.resolve(outside_file, workarea, api)

            self.assertEqual(api.get_page_calls, [])
            self.assertEqual(api.find_pages_by_title_calls, [])


class TestPageRefIDs(unittest.TestCase):

    def test_resolves_a_numeric_id_through_the_api(self) -> None:
        with temporary_workarea() as workarea:
            api = FakeAPI(page_ids=["123456"])

            page_ref = PageRef.resolve("123456", workarea, api, cwd=workarea.root_dir)

            self.assertEqual(page_ref.page_id, "123456")
            self.assertEqual(api.get_page_calls, ["123456"])
            self.assertEqual(api.find_pages_by_title_calls, [])


class TestPageRefTitles(unittest.TestCase):

    def test_resolves_a_unique_cached_title_without_an_api_request(self) -> None:
        with temporary_workarea() as workarea:
            state = example_page_state("123456", title="Example page")
            state.save(workarea.cache_path(state.page.id))
            api = FakeAPI()

            page_ref = PageRef.resolve("Example page", workarea, api, cwd=workarea.root_dir)

            self.assertEqual(page_ref.page_id, "123456")
            self.assertEqual(api.get_page_calls, [])
            self.assertEqual(api.find_pages_by_title_calls, [])

    def test_rejects_an_ambiguous_cached_title_without_an_api_request(self) -> None:
        with temporary_workarea() as workarea:
            first = example_page_state("123456", title="Duplicate", directory="First")
            second = example_page_state("234567", title="Duplicate", directory="Second")
            first.save(workarea.cache_path(first.page.id))
            second.save(workarea.cache_path(second.page.id))
            api = FakeAPI()

            with self.assertRaisesRegex(PageRefError, "123456.*234567"):
                PageRef.resolve("Duplicate", workarea, api, cwd=workarea.root_dir)

            self.assertEqual(api.get_page_calls, [])
            self.assertEqual(api.find_pages_by_title_calls, [])

    def test_resolves_one_exact_remote_title_candidate(self) -> None:
        with temporary_workarea() as workarea:
            api = FakeAPI(
                pages=[SimpleNamespace(id="123456", title="Example page"),
                       SimpleNamespace(id="234567", title="Example page copy")])

            page_ref = PageRef.resolve("Example page", workarea, api, cwd=workarea.root_dir)

            self.assertEqual(page_ref.page_id, "123456")
            self.assertEqual(api.find_pages_by_title_calls, ["Example page"])

    def test_rejects_zero_or_multiple_remote_title_candidates(self) -> None:
        with temporary_workarea() as workarea:
            no_match_api = FakeAPI()
            with self.assertRaisesRegex(PageRefError, "no page matches"):
                PageRef.resolve("Missing", workarea, no_match_api, cwd=workarea.root_dir)

            ambiguous_api = FakeAPI(
                pages=[SimpleNamespace(id="123456", title="Duplicate"),
                       SimpleNamespace(id="234567", title="Duplicate")])
            with self.assertRaisesRegex(PageRefError, "123456.*234567"):
                PageRef.resolve("Duplicate", workarea, ambiguous_api, cwd=workarea.root_dir)


# vim: set ts=4 sw=4 et tw=132:
