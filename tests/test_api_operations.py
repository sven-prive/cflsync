# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for typed Confluence page and attachment operations."""

import json
import unittest

from cflsync import APIClient, RemoteAttachment, RemotePage, RemoteUser
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
        "id": "att567890",
        "title": "diagram.png",
        "mediaType": "image/png",
        "fileId": "file-diagram",
        "version": {
            "number": 3},
        "_links": {
            "download": "/download/attachments/123456/diagram.png?version=3"}}


def user_fixture(account_id: str = "account-123", display_name: str | None = "Example User") -> dict[str, object]:
    return {"accountId": account_id, "email": "example.user@example.test", "displayName": display_name, "accountType": "atlassian"}


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

        self.assertEqual(attachment.id, "att567890")
        self.assertEqual(attachment.filename, "diagram.png")
        self.assertEqual(attachment.version, 3)
        self.assertEqual(attachment.media_type, "image/png")
        self.assertEqual(attachment.download_path, "/download/attachments/123456/diagram.png?version=3")

    def test_decodes_user_fields_needed_for_mention_resolution(self) -> None:
        user = RemoteUser.from_json(user_fixture())

        self.assertEqual(user.account_id, "account-123")
        self.assertEqual(user.email, "example.user@example.test")
        self.assertEqual(user.display_name, "Example User")


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

    def test_updates_a_page_title_while_preserving_its_body(self) -> None:
        updated = page_fixture(title="Renamed page")
        updated["version"] = {"number": 18}
        transport = MockTransport([MockResponse.from_json(updated)])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        page = RemotePage.from_json(client, page_fixture())

        result = page.update(page.body, "Renamed page")

        self.assertEqual(result.title, "Renamed page")
        self.assertEqual(result.version, 18)
        request = transport.requests[0]
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.path, "/pages/123456")
        self.assertEqual(
            json.loads(request.body), {
                "id": "123456",
                "status": "current",
                "title": "Renamed page",
                "body": {
                    "representation": "atlas_doc_format",
                    "value": '{"type":"doc","version":1,"content":[]}'},
                "version": {
                    "number": 18}})

    def test_updates_a_page_parent_while_preserving_title_and_body(self) -> None:
        updated = page_fixture()
        updated["parentId"] = "987654"
        updated["version"] = {"number": 18}
        transport = MockTransport([MockResponse.from_json(updated)])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        page = RemotePage.from_json(client, page_fixture())

        result = page.update(page.body, parent_id="987654")

        self.assertEqual(result.parent_id, "987654")
        request = transport.requests[0]
        self.assertEqual(json.loads(request.body)["title"], "Example page")
        self.assertEqual(json.loads(request.body)["parentId"], "987654")
        self.assertEqual(json.loads(request.body)["body"]["value"], page.body)

    def test_deletes_a_page(self) -> None:
        transport = MockTransport([MockResponse(204, {}, b"")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        page = RemotePage.from_json(client, page_fixture())

        page.delete()

        request = transport.requests[0]
        self.assertEqual(request.method, "DELETE")
        self.assertEqual(request.path, "/pages/123456")

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

    def test_finds_users_by_display_name_through_the_v1_user_search(self) -> None:
        first = user_fixture()
        second = user_fixture("account-456", None)
        transport = MockTransport([MockResponse.from_json({"results": [{"user": first}, {"user": second}]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        users = client.find_users_by_name('Example "User"')

        self.assertEqual([user.account_id for user in users], ["account-123", "account-456"])
        self.assertEqual(users[1].display_name, None)
        self.assertEqual(transport.clone_prefixes, ["/wiki/rest/api"])
        self.assertEqual(transport.requests[0].path, "/search/user")
        self.assertEqual(transport.requests[0].parameters, {"cql": 'user.fullname~"Example \\"User\\""'})

    def test_gets_a_user_by_account_id_through_the_v1_user_endpoint(self) -> None:
        transport = MockTransport([MockResponse.from_json(user_fixture())])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        user = client.get_user("account-123")

        self.assertEqual(user.account_id, "account-123")
        self.assertEqual(user.email, "example.user@example.test")
        self.assertEqual(transport.clone_prefixes, ["/wiki/rest/api"])
        self.assertEqual(transport.requests[0].path, "/user")
        self.assertEqual(transport.requests[0].parameters, {"accountId": "account-123"})

    def test_finds_a_user_only_when_name_and_email_have_one_match(self) -> None:
        first = user_fixture()
        second = user_fixture("account-456")
        second["email"] = "other.user@example.test"
        transport = MockTransport([MockResponse.from_json({"results": [{"user": first}, {"user": second}]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        user = client.find_user_by_name_and_email("Example User", "EXAMPLE.USER@example.test")

        self.assertEqual(user.account_id, "account-123")

    def test_does_not_choose_between_users_with_the_same_email(self) -> None:
        first = user_fixture()
        second = user_fixture("account-456")
        transport = MockTransport([MockResponse.from_json({"results": [{"user": first}, {"user": second}]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        self.assertIsNone(client.find_user_by_name_and_email("Example User", "example.user@example.test"))

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

        self.assertEqual([item.id for item in attachments], ["att567890"])
        self.assertEqual(transport.requests[0].path, "/pages/123456/attachments")

    def test_downloads_an_attachment_through_its_server_provided_link(self) -> None:
        transport = MockTransport([MockResponse(200, {"Content-Type": "image/png"}, b"PNG")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        attachment = RemoteAttachment.from_json(client, attachment_fixture(), "123456")

        self.assertEqual(attachment.download(), b"PNG")
        self.assertEqual(transport.clone_prefixes, ["/wiki"])
        self.assertEqual(transport.requests[0].path, attachment.download_path)

    def test_creates_an_attachment_with_a_multipart_request(self) -> None:
        attachment = attachment_fixture()
        transport = MockTransport([MockResponse.from_json({"results": [attachment]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        page = RemotePage.from_json(client, page_fixture())

        created = page.create_attachment("diagram.png", b"PNG")

        self.assertEqual(created.id, "att567890")
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
        self.assertEqual(transport.requests[0].path, "/content/123456/child/attachment/att567890/data")
        self.assertEqual(transport.requests[1].method, "DELETE")
        self.assertEqual(transport.requests[1].path, "/attachments/att567890")


# vim: set ts=4 sw=4 et tw=132:
