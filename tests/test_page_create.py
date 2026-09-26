# Copyright (c) 2026 Sven Rosiers
#
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
from tests.support import MockResponse, MockTransport, example_page_state, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


def created_page_fixture(page_id: str = "123456", title: str = "New page") -> dict[str, object]:
    page = page_fixture(page_id, title)
    page["body"] = {"atlas_doc_format": {"value": json.dumps({"type": "doc", "version": 1, "content": []})}}

    return page


class TestPageCreate(unittest.TestCase):

    def _create(self, workarea, responses, title="New page", parent_page_ref="456789"):
        transport = MockTransport(responses)
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        config = SimpleNamespace(profiles={workarea.profile: Profile("example.atlassian.net", "user", "token")})
        with patch("cflsync.cli.Path.cwd", return_value=workarea.root_dir):
            with patch("cflsync.cli.Config.find", return_value=config):
                with patch("cflsync.cli.APIClient", return_value=client):
                    status = PageCreateCommand().run(parent_page_ref, title)

        return transport, status

    def _pull_responses(self, page, attachments=None):
        if attachments is None:
            attachments = [attachment_fixture()]

        return [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": attachments}), *[MockResponse(200, {}, b"PNG") for attachment in attachments], ]

    def test_rejects_invalid_titles_before_any_request(self) -> None:
        for title in ("", "  ", "two\nlines", " padded "):
            with self.subTest(title=title):
                transport = MockTransport([])
                client = APIClient("example.atlassian.net", "user", "token", transport=transport)
                with patch("cflsync.cli.APIClient", return_value=client):
                    with self.assertRaises(SyncError):
                        PageCreateCommand().run("456789", title)

                self.assertEqual(transport.requests, [])

    def test_creates_a_child_page_and_installs_it_like_a_first_pull(self) -> None:
        with temporary_workarea() as workarea:
            page = created_page_fixture()
            parent = page_fixture("456789", "Parent page")
            responses = [
                MockResponse.from_json(parent),
                MockResponse.from_json(parent),
                MockResponse.from_json(page), *self._pull_responses(page)]

            transport, status = self._create(workarea, responses)

            state = PageState.load(workarea.cache_path("123456"))
            directory = workarea.page_directory(state)
            self.assertEqual(status, 0)
            self.assertEqual(state.page.title, "New page")
            self.assertEqual((directory / "page.md").read_text(), "# New page\n")
            self.assertEqual((directory / "_attachments/diagram.png").read_bytes(), b"PNG")
            create_request = transport.requests[2]
            self.assertEqual(create_request.method, "POST")
            self.assertEqual(create_request.path, "/pages")
            body = create_request.json_body()
            self.assertEqual(body["parentId"], "456789")
            self.assertEqual(body["spaceId"], "98765")
            self.assertEqual(body["title"], "New page")

    def test_failed_creation_leaves_no_local_state(self) -> None:
        with temporary_workarea() as workarea:
            parent = page_fixture("456789", "Parent page")
            responses = [
                MockResponse.from_json(parent),
                MockResponse.from_json(parent),
                MockResponse.from_json({"message": "title already exists"}, 400)]

            with self.assertRaises(SyncError):
                self._create(workarea, responses)

            self.assertEqual(list(workarea.page_state_paths()), [])
            self.assertEqual(list(workarea.root_dir.glob("*")), [workarea.root_dir / ".cflsync"])

    def test_resolves_a_parent_title_before_creation(self) -> None:
        with temporary_workarea() as workarea:
            page = created_page_fixture()
            parent = page_fixture("456789", "Parent page")
            responses = [
                MockResponse.from_json({"results": [parent]}),
                MockResponse.from_json(parent),
                MockResponse.from_json(page), *self._pull_responses(page), ]

            transport, status = self._create(workarea, responses, parent_page_ref="Parent page")

            self.assertEqual(status, 0)
            self.assertEqual(transport.requests[0].parameters["title"], "Parent page")
            self.assertEqual(transport.requests[2].json_body()["parentId"], "456789")

    def test_resolves_a_managed_parent_directory_before_creation(self) -> None:
        with temporary_workarea() as workarea:
            parent = page_fixture("456789", "Parent page")
            parent_state = example_page_state("456789", title="Parent page", directory="Parent page")
            parent_state.save(workarea.cache_path(parent_state.page.id))
            parent_directory = workarea.root_dir / parent_state.page.directory
            parent_directory.mkdir()
            (parent_directory / "page.md").write_text("# Parent page\n", encoding="utf-8")
            page = created_page_fixture()
            responses = [MockResponse.from_json(parent), MockResponse.from_json(page), *self._pull_responses(page)]

            transport, status = self._create(workarea, responses, parent_page_ref=str(parent_directory))

            self.assertEqual(status, 0)
            self.assertEqual(transport.requests[0].path, "/pages/456789")
            self.assertEqual(transport.requests[1].json_body()["parentId"], "456789")

    def test_failed_follow_up_pull_reports_the_created_page(self) -> None:
        with temporary_workarea() as workarea:
            page = created_page_fixture()
            parent = page_fixture("456789", "Parent page")
            responses = [
                MockResponse.from_json(parent),
                MockResponse.from_json(parent),
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json({"message": "attachments unavailable"}, 503),
                MockResponse.from_json({"message": "attachments unavailable"}, 503), ]

            with self.assertRaisesRegex(SyncError, "created page '123456'"):
                self._create(workarea, responses)

            self.assertEqual(list(workarea.page_state_paths()), [])
            self.assertEqual(list(workarea.root_dir.glob("*")), [workarea.root_dir / ".cflsync"])


# vim: set ts=4 sw=4 et tw=132:
