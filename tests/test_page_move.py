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

from cflsync import PageState, Profile, SyncError
from cflsync.cli import PageMoveCommand, PagePullCommand
from tests.support import FakeConfluence, temporary_workarea

BODY = json.dumps(
    {
        "type": "doc",
        "version": 1,
        "content": [{
            "type": "paragraph",
            "content": [{
                "type": "text",
                "text": "Example"}]}]})


class TestPageMove(unittest.TestCase):

    def setUp(self):
        self.site = self._site()

    def _site(self):
        site = FakeConfluence()
        site.add_page("456789", "Current parent")
        site.add_page("123456", "Example page", parent_id="456789", body=BODY, version=17)
        site.add_attachment("123456", "diagram.png", b"PNG")
        site.add_page("987654", "New parent")
        return site

    def _run(self, workarea, command):
        config = SimpleNamespace(profiles={workarea.profile: Profile("example.atlassian.net", "user", "token")})
        output = StringIO()
        with patch("cflsync.cli.Path.cwd", return_value=workarea.root_dir):
            with patch("cflsync.cli.Config.find", return_value=config):
                with patch("cflsync.cli.APIClient", return_value=self.site.client()):
                    with redirect_stdout(output):
                        status = command()

        return output.getvalue(), status

    def _pull(self, workarea):
        self._run(workarea, lambda: PagePullCommand().run("123456"))

    def _move(self, workarea, parent_id):
        self.site.requests.clear()
        return self._run(workarea, lambda: PageMoveCommand().run("123456", parent_id))

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def _without_cache(self, snapshot):
        return {path: value for path, value in snapshot.items() if not path.replace("\\", "/").startswith(".cflsync/cache/")}

    def test_moves_the_remote_page_and_updates_only_cached_version(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            _, status = self._move(workarea, "987654")

            state = PageState.load(workarea.cache_path("123456"))
            remote = self.site.content["123456"]
            self.assertEqual(status, 0)
            self.assertEqual(self._without_cache(self._snapshot(workarea)), self._without_cache(before))
            self.assertEqual((state.page.version, state.page.title, state.page.directory), (18, "Example page", "Example page"))
            self.assertEqual(state.page.parent_id, "987654")
            self.assertEqual(
                (remote["parent_id"], remote["version"], remote["title"], remote["body"]), ("987654", 18, "Example page", BODY))

    def test_rejects_local_or_remote_source_changes_without_moving(self) -> None:
        for local in [True, False]:
            with self.subTest(local=local):
                self.site = self._site()
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    if local:
                        (workarea.root_dir / "Example page/page.md").write_text("# Example page\n\nEdited\n", encoding="utf-8")
                    else:
                        self.site.content["123456"]["version"] = 18
                    before = self._snapshot(workarea)

                    with self.assertRaisesRegex(SyncError, "move conflicts"):
                        self._move(workarea, "987654")

                    self.assertEqual(self._snapshot(workarea), before)
                    self.assertEqual(self.site.content["123456"]["parent_id"], "456789")

    def test_rejects_self_and_cross_space_parents(self) -> None:
        self.site.add_page("555555", "Other space", space_id="other")
        for parent_id, error in [("123456", "cannot be its own parent"), ("555555", "different space")]:
            with self.subTest(parent=parent_id, error=error):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    before = self._snapshot(workarea)

                    with self.assertRaisesRegex(SyncError, error):
                        self._move(workarea, parent_id)

                    self.assertEqual(self._snapshot(workarea), before)
                    self.assertEqual(self.site.content["123456"]["parent_id"], "456789")

    def test_reports_server_hierarchy_rejection_without_changing_local_state(self) -> None:
        self.site.add_page("222222", "Descendant", parent_id="123456")
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            with self.assertRaisesRegex(SyncError, "cannot move page '123456' to parent '222222'.*invalid hierarchy"):
                self._move(workarea, "222222")

            self.assertEqual(self._snapshot(workarea), before)
            self.assertEqual(self.site.content["123456"]["parent_id"], "456789")

    def test_reports_an_unchanged_parent_as_a_noop(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            output, status = self._move(workarea, "456789")

            self.assertEqual(status, 0)
            self.assertIn("already a child", output)
            self.assertEqual(self._snapshot(workarea), before)
            self.assertTrue(all(request.method == "GET" for request in self.site.requests))

    def test_reports_incomplete_synchronization_when_cache_write_fails(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            with patch.object(PageState, "save", side_effect=SyncError("injected state failure")):
                with self.assertRaisesRegex(SyncError, "moved page '123456' remotely"):
                    self._move(workarea, "987654")

            self.assertEqual(self._snapshot(workarea), before)
            self.assertEqual(self.site.content["123456"]["parent_id"], "987654")


# vim: set ts=4 sw=4 et tw=132:
