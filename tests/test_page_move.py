# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Explicit remote page hierarchy move behavior."""

from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, PageState, Profile, SyncError
from cflsync.cli import PageMoveCommand, PagePullCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


class TestPageMove(unittest.TestCase):

    def _page(self, page_id="123456", version=17, title="Example page", parent_id="456789", space_id="98765"):
        page = page_fixture(page_id, title)
        page["version"] = {"number": version}
        page["spaceId"] = space_id
        if parent_id is None:
            del page["parentId"]
        else:
            page["parentId"] = parent_id
        page["body"] = {
            "atlas_doc_format": {
                "value":
                json.dumps(
                    {
                        "type": "doc",
                        "version": 1,
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Example"}]}]})}}

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
        responses = [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": [attachment_fixture()]}),
            MockResponse(200, {}, b"PNG"), ]
        self._run(workarea, lambda: PagePullCommand().run("123456"), responses)

    def _move(self, workarea, parent, source=None, updated=None):
        if source is None:
            source = self._page()
        if updated is None:
            updated = self._page(version=source["version"]["number"] + 1, parent_id=parent["id"])
        update_response = updated if isinstance(updated, MockResponse) else MockResponse.from_json(updated)

        responses = [
            MockResponse.from_json(source),
            MockResponse.from_json(source),
            MockResponse.from_json(parent),
            MockResponse.from_json(parent),
            MockResponse.from_json({"results": [attachment_fixture()]}), update_response, ]
        return self._run(workarea, lambda: PageMoveCommand().run("123456", parent["id"]), responses)

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_moves_the_remote_page_and_updates_only_cached_version(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            parent = self._page("987654", title="New parent", parent_id=None)

            _, status, transport = self._move(workarea, parent)

            state = PageState.load(workarea.cache_path("123456"))
            request = transport.requests[-1]
            self.assertEqual(status, 0)
            after = self._snapshot(workarea)
            self.assertEqual(
                {
                    path: value
                    for path, value in after.items() if not path.replace("\\", "/").startswith(".cflsync/cache/")}, {
                        path: value
                        for path, value in before.items() if not path.replace("\\", "/").startswith(".cflsync/cache/")})
            self.assertEqual(state.page.version, 18)
            self.assertEqual(state.page.title, "Example page")
            self.assertEqual(state.page.directory, "Example page")
            self.assertEqual(json.loads(request.body)["parentId"], "987654")
            self.assertEqual(json.loads(request.body)["title"], "Example page")
            self.assertEqual(json.loads(request.body)["body"]["value"], self._page()["body"]["atlas_doc_format"]["value"])

    def test_rejects_local_or_remote_source_changes_without_moving(self) -> None:
        for local, version in [(True, 17), (False, 18)]:
            with self.subTest(local=local, version=version):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    if local:
                        (workarea.root_dir / "Example page/page.md").write_text("# Example page\n\nEdited\n", encoding="utf-8")
                    before = self._snapshot(workarea)
                    parent = self._page("987654", title="New parent", parent_id=None)

                    with self.assertRaisesRegex(SyncError, "move conflicts"):
                        self._move(workarea, parent, source=self._page(version=version))

                    self.assertEqual(self._snapshot(workarea), before)

    def test_rejects_self_and_cross_space_parents(self) -> None:
        cases = [
            (self._page("123456", title="Example page", parent_id=None), "cannot be its own parent"),
            (self._page("987654", title="Other space", parent_id=None, space_id="other"), "different space"), ]
        for parent, error in cases:
            with self.subTest(parent=parent["id"], error=error):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    before = self._snapshot(workarea)

                    with self.assertRaisesRegex(SyncError, error):
                        self._move(workarea, parent)

                    self.assertEqual(self._snapshot(workarea), before)

    def test_reports_server_hierarchy_rejection_without_changing_local_state(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            parent = self._page("987654", title="Descendant", parent_id="123456")

            with self.assertRaisesRegex(SyncError, "cannot move page '123456' to parent '987654'"):
                self._move(workarea, parent, updated=MockResponse.from_json({"message": "invalid hierarchy"}, status=400))

            self.assertEqual(self._snapshot(workarea), before)

    def test_reports_an_unchanged_parent_as_a_noop(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            parent = self._page("456789", title="Current parent", parent_id=None)

            output, status, transport = self._move(workarea, parent)

            self.assertEqual(status, 0)
            self.assertIn("already a child", output)
            self.assertEqual(self._snapshot(workarea), before)
            self.assertTrue(all(request.method == "GET" for request in transport.requests))

    def test_reports_incomplete_synchronization_when_cache_write_fails(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            parent = self._page("987654", title="New parent", parent_id=None)

            with patch.object(PageState, "save", side_effect=SyncError("injected state failure")):
                with self.assertRaisesRegex(SyncError, "moved page '123456' remotely"):
                    self._move(workarea, parent)

            self.assertEqual(self._snapshot(workarea), before)


# vim: set ts=4 sw=4 et tw=132:
