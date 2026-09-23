# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Status reporting against a recorded HTTP transport."""

from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, Profile, SyncError
from cflsync.cli import PagePullCommand, PageStatusCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


def page_body(text="Example"):
    document = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}

    return {"atlas_doc_format": {"value": json.dumps(document)}}


class TestPageStatus(unittest.TestCase):

    def _page(self, version=17, title="Example page"):
        page = page_fixture(title=title)
        page["version"] = {"number": version}
        page["body"] = page_body()

        return page

    def _run(self, workarea, command, responses):
        transport = MockTransport(responses)
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        config = SimpleNamespace(profiles={workarea.profile: Profile("example.atlassian.net", "user", "token")})
        output = StringIO()
        with patch("cflsync.cli.Path.cwd", return_value=workarea.root_dir):
            with patch("cflsync.cli.Config.find", return_value=config):
                with patch("cflsync.cli.APIClient", return_value=client):
                    with redirect_stdout(output):
                        status = command()

        return output.getvalue(), status, transport

    def _pull(self, workarea, page=None, attachments=None):
        if page is None:
            page = self._page()

        if attachments is None:
            attachments = [attachment_fixture()]

        responses = [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": attachments}), *[MockResponse(200, {}, b"PNG") for attachment in attachments], ]
        self._run(workarea, lambda: PagePullCommand().run("123456"), responses)

    def _status(self, workarea, page=None, attachments=None):
        if page is None:
            page = self._page()

        if attachments is None:
            attachments = [attachment_fixture()]

        responses = [MockResponse.from_json(page), MockResponse.from_json(page), MockResponse.from_json({"results": attachments}), ]

        return self._run(workarea, lambda: PageStatusCommand().run("123456"), responses)

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_reports_both_sides_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)

            output, status, _ = self._status(workarea)

            self.assertEqual(status, 0)
            self.assertIn("Page '123456' (Example page)", output)
            self.assertIn("local:  unchanged", output)
            self.assertIn("remote: unchanged", output)

    def test_reports_local_page_and_attachment_changes(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "page.md").write_text("# Example page\n\nEdited\n")
            (directory / "_attachments/diagram.png").write_bytes(b"edited")

            output, _, _ = self._status(workarea)

            self.assertIn("local:  changed: page.md, _attachments/diagram.png", output)
            self.assertIn("remote: unchanged", output)

    def test_reports_a_referenced_new_attachment(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "_attachments/added.png").write_bytes(b"ADDED")
            (directory / "_attachments/ignored.png").write_bytes(b"IGNORED")
            with (directory / "page.md").open("a") as page_file:
                page_file.write("\n![Added](_attachments/added.png)\n")

            output, _, _ = self._status(workarea)

            self.assertIn("local:  changed: page.md, _attachments/added.png", output)

    def test_reports_remote_page_and_attachment_changes(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            attachment = attachment_fixture()
            attachment["version"] = {"number": 4}

            output, _, _ = self._status(workarea, page=self._page(version=18), attachments=[attachment])

            self.assertIn("local:  unchanged", output)
            self.assertIn("remote: changed: page, _attachments/diagram.png", output)

    def test_reports_changes_on_both_sides(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/page.md").write_text("# Example page\n\nEdited\n")

            output, _, _ = self._status(workarea, page=self._page(version=18))

            self.assertIn("local:  changed: page.md", output)
            self.assertIn("remote: changed: page", output)

    def test_reports_a_missing_page_directory_as_a_local_change(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "_attachments/diagram.png").unlink()
            (directory / "_attachments").rmdir()
            (directory / "page.md").unlink()
            directory.rmdir()

            output, _, _ = self._status(workarea)

            self.assertIn("local:  changed: page.md, _attachments/diagram.png", output)

    def test_rejects_a_page_without_a_cache_entry(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaisesRegex(SyncError, "not managed"):
                self._status(workarea)

    def test_changes_nothing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/page.md").write_text("# Example page\n\nEdited\n")
            before = self._snapshot(workarea)

            _, _, transport = self._status(workarea, page=self._page(version=18))

            self.assertEqual(self._snapshot(workarea), before)
            self.assertTrue(all(request.method == "GET" for request in transport.requests))

    def test_retried_status_read_changes_nothing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            page = self._page()
            responses = [
                MockResponse(503, {}, b""),
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json({"results": [attachment_fixture()]})]

            _, status, transport = self._run(workarea, lambda: PageStatusCommand().run("123456"), responses)

            self.assertEqual(status, 0)
            self.assertEqual(self._snapshot(workarea), before)
            self.assertEqual([request.method for request in transport.requests], ["GET", "GET", "GET", "GET"])


# vim: set ts=4 sw=4 et tw=132:
