# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Remote page creation followed by the normal pull path."""

import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, PageState, Profile, SyncError
from cflsync.cli import PageCreateCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


def created_page_fixture(page_id: str = "123456", title: str = "New page") -> dict[str, object]:
    page = page_fixture(page_id, title)
    page["body"] = {"atlas_doc_format": {"value": json.dumps({"type": "doc", "version": 1, "content": []})}}

    return page


class TestPageCreate(unittest.TestCase):

    def _create(self, workarea, responses, title="New page", parent_page_id="456789"):
        transport = MockTransport(responses)
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        config = SimpleNamespace(profiles={workarea.profile: Profile("example.atlassian.net", "user", "token")})
        with patch("cflsync.cli.Path.cwd", return_value=workarea.root_dir):
            with patch("cflsync.cli.Config.find", return_value=config):
                with patch("cflsync.cli.APIClient", return_value=client):
                    status = PageCreateCommand().run(parent_page_id, title)

        return transport, status

    def _pull_responses(self, page, attachments=None):
        if attachments is None:
            attachments = [attachment_fixture()]

        return [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": attachments}), *[MockResponse(200, {}, b"PNG") for attachment in attachments], ]

    def test_rejects_invalid_parent_ids_and_titles_before_any_request(self) -> None:
        cases = [("not-a-page", "New page"), ("456789", ""), ("456789", "  "), ("456789", "two\nlines"), ("456789", " padded ")]
        for parent_page_id, title in cases:
            with self.subTest(parent_page_id=parent_page_id, title=title):
                transport = MockTransport([])
                client = APIClient("example.atlassian.net", "user", "token", transport=transport)
                with patch("cflsync.cli.APIClient", return_value=client):
                    with self.assertRaises(SyncError):
                        PageCreateCommand().run(parent_page_id, title)

                self.assertEqual(transport.requests, [])

    def test_creates_a_child_page_and_installs_it_like_a_first_pull(self) -> None:
        with temporary_workarea() as workarea:
            page = created_page_fixture()
            parent = page_fixture("456789", "Parent page")
            responses = [MockResponse.from_json(parent), MockResponse.from_json(page), *self._pull_responses(page)]

            transport, status = self._create(workarea, responses)

            state = PageState.load(workarea.cache_path("123456"))
            directory = workarea.page_directory(state)
            self.assertEqual(status, 0)
            self.assertEqual(state.page.title, "New page")
            self.assertEqual((directory / "page.md").read_text(), "# New page\n")
            self.assertEqual((directory / "_attachments/diagram.png").read_bytes(), b"PNG")
            create_request = transport.requests[1]
            self.assertEqual(create_request.method, "POST")
            self.assertEqual(create_request.path, "/pages")
            body = json.loads(create_request.body)
            self.assertEqual(body["parentId"], "456789")
            self.assertEqual(body["spaceId"], "98765")
            self.assertEqual(body["title"], "New page")

    def test_failed_creation_leaves_no_local_state(self) -> None:
        with temporary_workarea() as workarea:
            parent = page_fixture("456789", "Parent page")
            responses = [MockResponse.from_json(parent), MockResponse.from_json({"message": "title already exists"}, 400)]

            with self.assertRaises(SyncError):
                self._create(workarea, responses)

            self.assertEqual(list(workarea.page_state_paths()), [])
            self.assertEqual(list(workarea.root_dir.glob("*")), [workarea.root_dir / ".cflsync"])

    def test_failed_follow_up_pull_reports_the_created_page(self) -> None:
        with temporary_workarea() as workarea:
            page = created_page_fixture()
            parent = page_fixture("456789", "Parent page")
            responses = [
                MockResponse.from_json(parent),
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json({"message": "attachments unavailable"}, 503), ]

            with self.assertRaisesRegex(SyncError, "created page '123456'"):
                self._create(workarea, responses)

            self.assertEqual(list(workarea.page_state_paths()), [])
            self.assertEqual(list(workarea.root_dir.glob("*")), [workarea.root_dir / ".cflsync"])


# vim: set ts=4 sw=4 et tw=132:
