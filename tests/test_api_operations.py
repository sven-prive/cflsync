# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for typed Confluence page and attachment operations."""

import json
import unittest

from cflsync import APIClient, RemoteAttachment, RemotePage
from tests.support import MockResponse, MockTransport


def page_fixture(page_id: str = "123456", title: str = "Example page") -> dict[str, object]:
    return {
        "id": page_id,
        "title": title,
        "spaceId": "98765",
        "parentId": "456789",
        "version": {
            "number": 17},
        "body": {
            "atlas_doc_format": {
                "value": '{"type":"doc","version":1,"content":[]}'}}}


def attachment_fixture() -> dict[str, object]:
    return {
        "id": "567890",
        "title": "diagram.png",
        "mediaType": "image/png",
        "version": {
            "number": 3},
        "_links": {
            "download": "/download/attachments/123456/diagram.png?version=3"}}


class TestRemoteModels(unittest.TestCase):

    def test_decodes_page_fields_needed_for_synchronization(self) -> None:
        client = APIClient("example.atlassian.net", "user", "token", transport=MockTransport())
        page = RemotePage.from_json(client, page_fixture())

        self.assertEqual(page.id, "123456")
        self.assertEqual(page.title, "Example page")
        self.assertEqual(page.version, 17)
        self.assertEqual(page.body, '{"type":"doc","version":1,"content":[]}')
        self.assertEqual(page.space_id, "98765")
        self.assertEqual(page.parent_id, "456789")

    def test_decodes_attachment_fields_needed_for_synchronization(self) -> None:
        client = APIClient("example.atlassian.net", "user", "token", transport=MockTransport())
        attachment = RemoteAttachment.from_json(client, attachment_fixture())

        self.assertEqual(attachment.id, "567890")
        self.assertEqual(attachment.filename, "diagram.png")
        self.assertEqual(attachment.version, 3)
        self.assertEqual(attachment.media_type, "image/png")
        self.assertEqual(attachment.download_path, "/download/attachments/123456/diagram.png?version=3")


class TestAPIClientPageOperations(unittest.TestCase):

    def test_get_page_requests_atlas_doc_format(self) -> None:
        transport = MockTransport([MockResponse.from_json(page_fixture())])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        page = client.get_page("123456")

        self.assertEqual(page.id, "123456")
        self.assertEqual(page.version, 17)
        request = transport.requests[0]
        self.assertEqual(request.method, "GET")
        self.assertEqual(request.path, "/pages/123456")
        self.assertEqual(request.parameters, {"body-format": "atlas_doc_format", "include-version": "true"})

    def test_finds_all_title_candidates(self) -> None:
        first = page_fixture("1", "Duplicate")
        second = page_fixture("2", "Duplicate")
        similar = page_fixture("3", "Duplicate copy")
        transport = MockTransport([MockResponse.from_json({"results": [first, second, similar]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        pages = client.find_pages_by_title("Duplicate")

        self.assertEqual([page.id for page in pages], ["1", "2"])
        self.assertEqual(
            transport.requests[0].parameters, {
                "title": "Duplicate",
                "body-format": "atlas_doc_format",
                "include-version": "true"})

    def test_creates_an_empty_child_page(self) -> None:
        transport = MockTransport([MockResponse.from_json(page_fixture())])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        page = client.create_page("98765", "456789", "Example page")

        self.assertEqual(page.id, "123456")
        request = transport.requests[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.path, "/pages")
        self.assertEqual(
            json.loads(request.body), {
                "spaceId": "98765",
                "status": "current",
                "title": "Example page",
                "parentId": "456789",
                "body": {
                    "representation": "atlas_doc_format",
                    "value": '{"type":"doc","version":1,"content":[]}'}})

    def test_updates_a_page_with_the_next_version(self) -> None:
        updated = page_fixture()
        updated["version"] = {"number": 18}
        transport = MockTransport([MockResponse.from_json(updated)])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        current = RemotePage.from_json(client, page_fixture())

        page = current.update('{"type":"doc","version":1,"content":[{"type":"paragraph"}]}')

        self.assertEqual(page.version, 18)
        request = transport.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.path, "/pages/123456")
        self.assertEqual(json.loads(request.body)["version"], {"number": 18})


class TestAPIClientAttachmentOperations(unittest.TestCase):

    def test_lists_all_attachments(self) -> None:
        attachment = attachment_fixture()
        transport = MockTransport([MockResponse.from_json({"results": [attachment]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        page = RemotePage.from_json(client, page_fixture())
        attachments = page.attachments()

        self.assertEqual([item.id for item in attachments], ["567890"])
        self.assertEqual(transport.requests[0].path, "/pages/123456/attachments")

    def test_downloads_an_attachment_through_its_server_provided_link(self) -> None:
        transport = MockTransport([MockResponse(200, {"Content-Type": "image/png"}, b"PNG")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        attachment = RemoteAttachment.from_json(client, attachment_fixture(), "123456")

        self.assertEqual(attachment.download(), b"PNG")
        self.assertEqual(transport.clone_prefixes, [""])
        self.assertEqual(transport.requests[0].path, attachment.download_path)

    def test_creates_an_attachment_with_a_multipart_request(self) -> None:
        attachment = attachment_fixture()
        transport = MockTransport([MockResponse.from_json({"results": [attachment]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        page = RemotePage.from_json(client, page_fixture())

        created = page.create_attachment("diagram.png", b"PNG")

        self.assertEqual(created.id, "567890")
        self.assertEqual(transport.clone_prefixes, ["/wiki/rest/api"])
        request = transport.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.path, "/content/123456/child/attachment")
        self.assertEqual(request.headers["X-Atlassian-Token"], "nocheck")
        self.assertIn(b'filename="diagram.png"', request.body)
        self.assertIn(b"\r\nPNG\r\n", request.body)

    def test_updates_and_deletes_an_attachment(self) -> None:
        attachment = attachment_fixture()
        transport = MockTransport([MockResponse.from_json(attachment), MockResponse(204, {}, b"")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        remote_attachment = RemoteAttachment.from_json(client, attachment, "123456")

        updated = remote_attachment.update(b"new PNG")
        updated.delete()

        self.assertEqual(updated.version, 3)
        self.assertEqual(transport.clone_prefixes, ["/wiki/rest/api"])
        self.assertEqual(transport.requests[0].method, "POST")
        self.assertEqual(transport.requests[0].path, "/content/123456/child/attachment/567890/data")
        self.assertEqual(transport.requests[1].method, "DELETE")
        self.assertEqual(transport.requests[1].path, "/attachments/567890")


# vim: set ts=4 sw=4 et tw=132:
