# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for the in-memory Confluence site fixture, exercised through the API client."""

import json
import unittest

from cflsync import APIError
from tests.support import FakeConfluence


class TestFakeConfluencePages(unittest.TestCase):

    def test_serves_a_page_with_its_body_version_and_parent(self) -> None:
        site = FakeConfluence()
        site.add_folder("100", "Folder")
        site.add_page("200", "Parent", parent_id="100", body='{"type":"doc","version":1,"content":[]}', version=4)

        page = site.client().get_page("200")

        self.assertEqual((page.id, page.title, page.version, page.parent_id, page.space_id), ("200", "Parent", 4, "100", "98765"))
        self.assertEqual(page.body, '{"type":"doc","version":1,"content":[]}')

    def test_reports_missing_pages_and_folders_requested_as_pages(self) -> None:
        site = FakeConfluence()
        site.add_folder("100", "Folder")

        for page_id in ["100", "999"]:
            with self.subTest(page_id=page_id):
                with self.assertRaises(APIError) as context:
                    site.client().get_page(page_id)

                self.assertEqual(context.exception.status, 404)

    def test_finds_pages_by_exact_title(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Duplicate")
        site.add_page("200", "Duplicate")
        site.add_page("300", "Duplicate copy")

        pages = site.client().find_pages_by_title("Duplicate")

        self.assertEqual([page.id for page in pages], ["100", "200"])

    def test_creates_a_child_page(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Parent")

        page = site.client().create_page("98765", "100", "Child")

        self.assertEqual((page.title, page.parent_id, page.version), ("Child", "100", 1))
        self.assertEqual(site.content[page.id]["parent_id"], "100")

    def test_updates_title_body_and_parent_with_the_next_version(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Old parent")
        site.add_page("200", "New parent")
        site.add_page("300", "Page", parent_id="100", version=7)
        page = site.client().get_page("300")

        updated = page.update('{"type":"doc","version":1,"content":[{"type":"rule"}]}', "Renamed", "200")

        self.assertEqual((updated.title, updated.parent_id, updated.version), ("Renamed", "200", 8))
        self.assertEqual(site.content["300"]["body"], '{"type":"doc","version":1,"content":[{"type":"rule"}]}')

    def test_rejects_an_update_based_on_a_stale_version(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page", version=7)
        page = site.client().get_page("100")
        site.content["100"]["version"] = 8

        with self.assertRaises(APIError) as context:
            page.update(site.content["100"]["body"])

        self.assertEqual(context.exception.status, 409)
        self.assertEqual(site.content["100"]["version"], 8)

    def test_rejects_moves_below_the_page_itself(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        site.add_page("200", "Child", parent_id="100")
        page = site.client().get_page("100")

        for parent_id in ["100", "200"]:
            with self.subTest(parent_id=parent_id):
                with self.assertRaises(APIError) as context:
                    page.update(site.content["100"]["body"], parent_id=parent_id)

                self.assertEqual(context.exception.status, 400)
                self.assertIsNone(site.content["100"]["parent_id"])

    def test_deletes_a_leaf_page_and_its_attachments(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        site.add_attachment("100", "diagram.png", b"PNG")

        site.client().get_page("100").delete()

        self.assertEqual((site.content, site.attachments), ({}, {}))

    def test_refuses_to_guess_how_deleting_a_parent_page_behaves(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Parent")
        site.add_page("200", "Child", parent_id="100")
        page = site.client().get_page("100")

        with self.assertRaisesRegex(AssertionError, "which has children"):
            page.delete()


class TestFakeConfluenceHierarchy(unittest.TestCase):

    def _tree(self):
        site = FakeConfluence()
        site.add_page("100", "Space home")
        site.add_folder("200", "Folder", parent_id="100")
        site.add_page("300", "Root", parent_id="200")
        site.add_page("400", "Child", parent_id="300")
        return site

    def test_lists_ancestors_highest_first_including_folders(self) -> None:
        ancestors = self._tree().client().page_ancestors("400")

        self.assertEqual(
            [(ancestor.id, ancestor.type) for ancestor in ancestors], [("100", "page"), ("200", "folder"), ("300", "page")])

    def test_limits_ancestors_to_the_nearest_and_serves_folder_ancestors(self) -> None:
        site = self._tree()
        client = site.client()

        nearest = json.loads(client.make_request("GET", "/pages/400/ancestors", parameters={"limit": "1"}).body)
        above_folder = json.loads(client.make_request("GET", "/folders/200/ancestors").body)

        self.assertEqual(nearest, {"results": [{"id": "300", "type": "page"}]})
        self.assertEqual(above_folder, {"results": [{"id": "100", "type": "page"}]})

    def test_rejects_listing_limits_above_the_confluence_maximum(self) -> None:
        client = self._tree().client()

        for path in ["/pages/400/ancestors", "/pages/300/direct-children"]:
            with self.subTest(path=path):
                with self.assertRaises(APIError) as context:
                    client.make_request("GET", path, parameters={"limit": "251"})

                self.assertEqual(context.exception.status, 400)

    def test_lists_direct_children_across_listing_pages(self) -> None:
        site = FakeConfluence(page_size=1)
        site.add_page("100", "Parent")
        site.add_page("200", "First", parent_id="100")
        site.add_folder("300", "Second", parent_id="100")
        site.add_page("400", "Grandchild", parent_id="200")

        children = site.client().page_children("100")

        self.assertEqual(
            [(child.id, child.type, child.title) for child in children], [("200", "page", "First"), ("300", "folder", "Second")])
        self.assertEqual(
            [request.path for request in site.requests],
            ["/wiki/api/v2/pages/100/direct-children", "/wiki/api/v2/pages/100/direct-children?limit=250&cursor=1"])


class TestFakeConfluenceAttachments(unittest.TestCase):

    def test_lists_and_downloads_attachments(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        site.add_attachment("100", "diagram one.png", b"PNG", attachment_id="att1", version=3)

        attachments = site.client().get_page("100").attachments()

        self.assertEqual(
            [(item.id, item.filename, item.version, item.file_id) for item in attachments], [
                ("att1", "diagram one.png", 3, "file-att1")])
        self.assertEqual(attachments[0].download(), b"PNG")

    def test_creates_updates_and_deletes_an_attachment(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        page = site.client().get_page("100")

        created = page.create_attachment("report.pdf", b"PDF")
        updated = created.update(b"PDF2")
        stored = dict(site.attachments[created.id])
        updated.delete()

        self.assertEqual((updated.id, updated.version), (created.id, 2))
        self.assertEqual((stored["filename"], stored["body"]), ("report.pdf", b"PDF2"))
        self.assertEqual(site.attachments, {})


class TestFakeConfluenceFailures(unittest.TestCase):

    def test_injects_a_failure_for_the_next_matching_request_only(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        site.fail("GET", "/wiki/api/v2/pages/100", 403)
        client = site.client()

        with self.assertRaises(APIError) as context:
            client.get_page("100")

        self.assertEqual(context.exception.status, 403)
        self.assertEqual(client.get_page("100").id, "100")

    def test_a_transient_failure_is_retried_by_the_client(self) -> None:
        site = FakeConfluence()
        site.add_page("100", "Page")
        site.fail("GET", "/wiki/api/v2/pages/100", 503)

        self.assertEqual(site.client().get_page("100").id, "100")
        self.assertEqual(len(site.requests), 2)

    def test_fails_a_listing_that_is_truncated_after_its_first_page(self) -> None:
        site = FakeConfluence(page_size=1)
        site.add_page("100", "Parent")
        site.add_page("200", "First", parent_id="100")
        site.add_page("300", "Second", parent_id="100")
        site.fail("GET", "/wiki/api/v2/pages/100/direct-children?limit=250&cursor=1", 500)

        with self.assertRaises(APIError) as context:
            site.client().page_children("100")

        self.assertEqual(context.exception.status, 500)

    def test_an_unmodelled_request_fails_the_test(self) -> None:
        site = FakeConfluence()

        with self.assertRaisesRegex(AssertionError, "does not model GET /wiki/rest/api/user"):
            site.client().get_user("account-123")


# vim: set ts=4 sw=4 et tw=132:
