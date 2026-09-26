# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Explicit local and remote page removal behavior."""

from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, Profile, SyncError
from cflsync.cli import PagePullCommand, PageRemoveCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


class TestPageRemove(unittest.TestCase):

    def _page(self, version=17):
        page = page_fixture()
        page["version"] = {"number": version}
        page["body"] = {"atlas_doc_format": {"value": json.dumps({"type": "doc", "version": 1, "content": []})}}
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

    def _pull(self, workarea):
        page = self._page()
        self._run(
            workarea, lambda: PagePullCommand().run("123456"), [
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json({"results": [attachment_fixture()]}),
                MockResponse(200, {}, b"PNG"), ])

    def _remove(self, workarea, force=True, page=None, attachments=None, delete_response=None):
        if page is None:
            page = self._page()
        if attachments is None:
            attachments = [attachment_fixture()]
        if delete_response is None:
            delete_response = MockResponse(204, {}, b"")

        responses = [MockResponse.from_json(page), MockResponse.from_json({"results": attachments}), delete_response, ]
        return self._run(workarea, lambda: PageRemoveCommand().run("123456", force=force), responses)

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_removes_remote_page_local_directory_and_cache(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/notes.txt").write_text("unmanaged", encoding="utf-8")

            _, status, transport = self._remove(workarea)

            self.assertEqual(status, 0)
            self.assertEqual(transport.requests[-1].method, "DELETE")
            self.assertEqual(transport.requests[-1].path, "/pages/123456")
            self.assertFalse((workarea.root_dir / "Example page").exists())
            self.assertFalse(workarea.cache_path("123456").exists())

    def test_confirms_before_removing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)

            with patch("builtins.input", return_value="yes") as confirm:
                _, status, _ = self._remove(workarea, force=False)

            self.assertEqual(status, 0)
            self.assertIn("Remove remote and local copy of page 'Example page' (123456)", confirm.call_args.args[0])
            self.assertFalse((workarea.root_dir / "Example page").exists())

    def test_declining_confirmation_changes_nothing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            with patch("builtins.input", return_value="no"):
                _, status, transport = self._remove(workarea, force=False)

            self.assertEqual(status, 0)
            self.assertEqual(self._snapshot(workarea), before)
            self.assertTrue(all(request.method == "GET" for request in transport.requests))

    def test_force_bypasses_confirmation(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)

            with patch("builtins.input", side_effect=AssertionError("unexpected confirmation")):
                _, status, _ = self._remove(workarea, force=True)

            self.assertEqual(status, 0)

    def test_removes_local_copy_when_remote_page_is_already_missing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            responses = [MockResponse.from_json({"message": "not found"}, status=404)]

            with patch("builtins.input", return_value="yes") as confirm:
                _, status, transport = self._run(workarea, lambda: PageRemoveCommand().run("123456"), responses)

            self.assertEqual(status, 0)
            self.assertEqual([request.method for request in transport.requests], ["GET"])
            self.assertIn("local copy", confirm.call_args.args[0])
            self.assertFalse((workarea.root_dir / "Example page").exists())
            self.assertFalse(workarea.cache_path("123456").exists())

    def test_requires_a_local_managed_page(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaisesRegex(SyncError, "no managed local page"):
                self._run(workarea, lambda: PageRemoveCommand().run("123456", force=True), [])

    def test_rejects_unsynchronized_pages_without_removing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/content.md").write_text("# Example page\n\nEdited\n", encoding="utf-8")
            before = self._snapshot(workarea)

            with self.assertRaisesRegex(SyncError, "remove conflicts"):
                self._remove(workarea)

            self.assertEqual(self._snapshot(workarea), before)

    def test_remote_delete_failure_leaves_local_state_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            with self.assertRaisesRegex(SyncError, "delete rejected"):
                self._remove(workarea, delete_response=MockResponse.from_json({"message": "delete rejected"}, status=500))

            self.assertEqual(self._snapshot(workarea), before)


# vim: set ts=4 sw=4 et tw=132:
