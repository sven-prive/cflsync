# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Explicit page title and directory rename behavior."""

from contextlib import redirect_stdout
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, PageState, Profile, SyncError
from cflsync.cli import PagePullCommand, PageRenameCommand
from tests.support import MockResponse, MockTransport, example_page_state, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


class TestPageRename(unittest.TestCase):

    def _page(self, version=17, title="Example page"):
        page = page_fixture(title=title)
        page["version"] = {"number": version}
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

    def _rename(self, workarea, title, page=None, attachments=None, updated=None):
        if page is None:
            page = self._page()
        if attachments is None:
            attachments = [attachment_fixture()]
        if updated is None:
            updated = self._page(page["version"]["number"] + 1, title)

        return self._run(
            workarea, lambda: PageRenameCommand().run("123456", title), [
                MockResponse.from_json(page),
                MockResponse.from_json(page),
                MockResponse.from_json({"results": attachments}),
                MockResponse.from_json(updated), ])

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_renames_the_remote_page_heading_directory_and_cache(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            source = workarea.root_dir / "Example page"
            (source / "notes.txt").write_text("unmanaged", encoding="utf-8")

            _, status, transport = self._rename(workarea, "Renamed page")

            state = PageState.load(workarea.cache_path("123456"))
            target = workarea.root_dir / "Renamed page"
            request = transport.requests[-1]
            self.assertEqual(status, 0)
            self.assertFalse(source.exists())
            self.assertEqual((target / "page.md").read_text(encoding="utf-8"), "# Renamed page\n\nExample\n")
            self.assertEqual((target / "_attachments/diagram.png").read_bytes(), b"PNG")
            self.assertEqual((target / "notes.txt").read_text(encoding="utf-8"), "unmanaged")
            self.assertEqual((state.page.title, state.page.directory, state.page.version), ("Renamed page", "Renamed page", 18))
            self.assertEqual(request.json_body()["title"], "Renamed page")
            self.assertEqual(request.json_body()["body"]["value"], self._page()["body"]["atlas_doc_format"]["value"])

    def test_rejects_unsynchronized_local_or_remote_pages_without_mutation(self) -> None:
        for local, version in [(True, 17), (False, 18)]:
            with self.subTest(local=local, version=version):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    if local:
                        (workarea.root_dir / "Example page/page.md").write_text("# Example page\n\nEdited\n", encoding="utf-8")
                    before = self._snapshot(workarea)

                    with self.assertRaisesRegex(SyncError, "rename conflicts"):
                        self._rename(workarea, "Renamed page", page=self._page(version))

                    self.assertEqual(self._snapshot(workarea), before)

    def test_rejects_a_target_owned_by_another_cached_page_before_remote_update(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            other = example_page_state("234567", title="Other page", directory="Renamed page")
            other.save(workarea.cache_path(other.page.id))

            with self.assertRaisesRegex(SyncError, "assigned to page '234567'"):
                self._rename(workarea, "Renamed page")

    def test_rejects_invalid_titles_before_opening_the_workarea(self) -> None:
        for title in ["", " leading", "trailing ", "line\nbreak", "tab\tcharacter"]:
            with self.subTest(title=title):
                with self.assertRaisesRegex(SyncError, "page title must be"):
                    PageRenameCommand().run("123456", title)

    def test_remote_update_failure_leaves_local_state_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            responses = [
                MockResponse.from_json(self._page()),
                MockResponse.from_json(self._page()),
                MockResponse.from_json({"results": [attachment_fixture()]}),
                MockResponse.from_json({"message": "update rejected"}, 500), ]

            with self.assertRaises(SyncError):
                self._run(workarea, lambda: PageRenameCommand().run("123456", "Renamed page"), responses)

            self.assertEqual(self._snapshot(workarea), before)

    def test_state_write_failure_rolls_back_the_local_rename(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            with patch.object(PageState, "save", side_effect=SyncError("injected state failure")):
                with self.assertRaisesRegex(SyncError, "injected state failure"):
                    self._rename(workarea, "Renamed page")

            self.assertEqual(self._snapshot(workarea), before)

    def test_rejects_a_windows_current_directory_before_remote_update(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            responses = [
                MockResponse.from_json(self._page()),
                MockResponse.from_json(self._page()),
                MockResponse.from_json({"results": [attachment_fixture()]}), ]

            with patch("cflsync.workarea._is_windows", return_value=True):
                with patch("cflsync.workarea._current_directory_is_inside", return_value=True):
                    with self.assertRaisesRegex(SyncError, "current directory"):
                        self._run(workarea, lambda: PageRenameCommand().run("123456", "Renamed page"), responses)

            self.assertEqual(self._snapshot(workarea), before)


# vim: set ts=4 sw=4 et tw=132:
