# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for typed page synchronization state and the cached page tree."""

import hashlib
import unittest

from cflsync import AttachmentMetadata, PageMetadata, PageState, StateError
from cflsync.workarea import PageTree
from tests.support import example_page_state, temporary_workarea

PAGE_HASH = hashlib.sha256(b"page").hexdigest()


def page_metadata(id="123456", title="Example page", parent_id=None, directory="Example page", content_hash=PAGE_HASH):
    return PageMetadata(id, title, parent_id, directory, 17, content_hash)


class TestPageStateSerialization(unittest.TestCase):

    def test_metadata_constructor_rejects_an_invalid_content_hash(self) -> None:
        with self.assertRaises(StateError):
            page_metadata(content_hash="not-a-hash")

    def test_metadata_accepts_a_numeric_parent_or_none(self) -> None:
        self.assertIsNone(page_metadata().parent_id)
        self.assertEqual(page_metadata(parent_id="456789").parent_id, "456789")

    def test_metadata_rejects_an_invalid_parent_id(self) -> None:
        for parent_id in ["", "abc", "12 3"]:
            with self.subTest(parent_id=parent_id):
                with self.assertRaisesRegex(StateError, "page.parent_id"):
                    page_metadata(parent_id=parent_id)

    def test_metadata_requires_a_single_directory_name(self) -> None:
        for directory in [".", "..", "Parent/Child", "Parent\\Child", "../outside", "Nul\x00name"]:
            with self.subTest(directory=directory):
                with self.assertRaisesRegex(StateError, "single directory name"):
                    page_metadata(directory=directory)

    def test_attachment_metadata_accepts_an_opaque_remote_id(self) -> None:
        attachment_hash = hashlib.sha256(b"attachment").hexdigest()
        attachment = AttachmentMetadata(id="att1843529704", version=1, content_hash=attachment_hash)

        self.assertEqual(attachment.id, "att1843529704")
        with self.assertRaises(StateError):
            AttachmentMetadata(id="att 1843529704", version=1, content_hash=attachment_hash)

    def test_serializes_the_format_2_state_shape(self) -> None:
        attachment_hash = hashlib.sha256(b"attachment").hexdigest()
        state = PageState(
            page=page_metadata(id="123457", title="Child", parent_id="123456", directory="Child"),
            attachments={"diagram.png": AttachmentMetadata(id="att987654", version=3, content_hash=attachment_hash)})

        self.assertEqual(
            state.to_json(), {
                "format": 2,
                "page": {
                    "id": "123457",
                    "title": "Child",
                    "parent_id": "123456",
                    "directory": "Child",
                    "version": 17,
                    "content_hash": PAGE_HASH},
                "attachments": {
                    "diagram.png": {
                        "id": "att987654",
                        "version": 3,
                        "content_hash": attachment_hash}}})

    def test_round_trips_a_root_and_a_child_state(self) -> None:
        for state in [example_page_state(), example_page_state("123457", "Child", "Child", "123456")]:
            with self.subTest(page_id=state.page.id):
                self.assertEqual(PageState.from_json(state.to_json()), state)

    def test_rejects_format_1_state(self) -> None:
        value = example_page_state().to_json()
        value["format"] = 1

        with self.assertRaisesRegex(StateError, "unsupported state format 1"):
            PageState.from_json(value)

    def test_requires_the_parent_id_field(self) -> None:
        page = example_page_state().page.to_json()
        del page["parent_id"]

        with self.assertRaisesRegex(StateError, "page.parent_id is required"):
            PageState.from_json({"format": 2, "page": page, "attachments": {}})


class TestPageTree(unittest.TestCase):

    def _states(self, *pages):
        return {
            page_id: example_page_state(page_id, directory=directory, parent_id=parent_id)
            for page_id, directory, parent_id in pages}

    def test_derives_directories_from_the_chain_of_cached_parents(self) -> None:
        tree = PageTree(
            self._states(("1", "Root", None), ("2", "Child", "1"), ("3", "Grandchild", "2"), ("4", "Sibling", "1")), "1")

        self.assertEqual(
            {page_id: tree.directory(page_id)
             for page_id in ["1", "2", "3", "4"]}, {
                 "1": "Root",
                 "2": "Root/Child",
                 "3": "Root/Child/Grandchild",
                 "4": "Root/Sibling"})

    def test_accepts_an_empty_cache(self) -> None:
        tree = PageTree({}, "1")

        with self.assertRaisesRegex(StateError, "page '1' is not cached"):
            tree.directory("1")

    def test_rejects_broken_chains(self) -> None:
        cases = [
            ((("1", "Root", None), ("3", "Grandchild", "2")), "cached parent page '2' of page '3' is missing"),
            ((("2", "Child", "1"), ), "cached parent page '1' of page '2' is missing"),
            ((("1", "Root", None), ("2", "Other root", None)), "page '2' has no parent but is not the root page '1'"),
            ((("1", "Root", "9"), ("9", "Above root", None)), "root page '1' must not have a cached parent"),
            ((("1", "Root", None), ("2", "First", "3"), ("3", "Second", "2")), "the cached parents of page '2' form a cycle"), ]
        for pages, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(StateError, error):
                    PageTree(self._states(*pages), "1")

    def test_loads_the_tree_from_the_workarea_cache(self) -> None:
        with temporary_workarea(root_page_id="1") as workarea:
            for state in self._states(("1", "Root", None), ("2", "Child", "1")).values():
                state.save(workarea.cache_path(state.page.id))

            tree = workarea.page_tree()

            self.assertEqual((tree.root_page_id, tree.directory("2")), ("1", "Root/Child"))
            self.assertEqual(workarea.page_directory_path(tree.directory("2")), workarea.root_dir / "Root" / "Child")


# vim: set ts=4 sw=4 et tw=132:
