# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pull decisions and rollback against a recorded HTTP transport."""

import hashlib
from contextlib import redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, PageState, Profile, SyncError
from cflsync.cli import PagePullCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture


class TestPagePull(unittest.TestCase):

    def _page(self, version=17, title="Example page"):
        page = page_fixture(title=title)
        page["version"] = {"number": version}
        document = {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "Example"}]}]}
        page["body"] = {"atlas_doc_format": {"value": json.dumps(document)}}

        return page

    def _pull(self, workarea, page=None, attachments=None, downloads=None, force=False):
        if page is None:
            page = self._page()

        if attachments is None:
            attachments = [attachment_fixture()]

        if downloads is None:
            downloads = [MockResponse(200, {}, b"PNG") for attachment in attachments]

        responses = [MockResponse.from_json(page), MockResponse.from_json(page)]
        # Exercise actual pagination, including an empty final page.
        responses.append(MockResponse.from_json({"results": attachments, "_links": {"next": "/next"}}))
        responses.append(MockResponse.from_json({"results": []}))
        responses.extend(downloads)
        transport = MockTransport(responses)
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)
        config = SimpleNamespace(profiles={workarea.profile: Profile("example.atlassian.net", "user", "token")})
        with patch("cflsync.cli.Path.cwd", return_value=workarea.root_dir):
            with patch("cflsync.cli.Config.find", return_value=config):
                with patch("cflsync.cli.APIClient", return_value=client):
                    self.assertEqual(PagePullCommand().run("123456", force=force), 0)

        self.assertTrue(all(request.method == "GET" for request in transport.requests))

        return transport

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_first_pull_installs_content_attachments_and_private_state(self) -> None:
        with temporary_workarea() as workarea:
            transport = self._pull(workarea)
            state = PageState.load(workarea.cache_path("123456"))
            directory = workarea.page_directory(state)

            self.assertEqual((directory / "page.md").read_text(), "# Example page\n\nExample\n")
            self.assertEqual((directory / "_attachments/diagram.png").read_bytes(), b"PNG")
            self.assertEqual(state.page.content_hash, hashlib.sha256(b"# Example page\n\nExample\n").hexdigest())
            self.assertEqual(state.attachments["diagram.png"].content_hash, hashlib.sha256(b"PNG").hexdigest())
            self.assertEqual(workarea.cache_path("123456").stat().st_mode & 0o777, 0o600)
            self.assertIn("/next", [request.path for request in transport.requests])

    def test_unchanged_and_formatting_only_changes_are_noops(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            for markdown in ["# Example page\n\nExample\n", "# Example page\n\nExample\n\n\n"]:
                with self.subTest(markdown=markdown):
                    (workarea.root_dir / "Example page/page.md").write_text(markdown)
                    before = self._snapshot(workarea)
                    output = StringIO()
                    with redirect_stdout(output):
                        transport = self._pull(workarea, downloads=[])

                    self.assertEqual(self._snapshot(workarea), before)
                    self.assertEqual(len(transport.requests), 4)
                    self.assertIn("already in sync; nothing pulled", output.getvalue())

    def test_force_regenerates_unchanged_content(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            output = StringIO()
            with patch("cflsync.cli.ADFToMarkdownConverter.convert", return_value="# Example page\n\nRegenerated\n"):
                with redirect_stdout(output):
                    transport = self._pull(workarea, force=True)

            state = PageState.load(workarea.cache_path("123456"))
            markdown = (workarea.page_directory(state) / "page.md").read_bytes()
            self.assertEqual(markdown, b"# Example page\n\nRegenerated\n")
            self.assertEqual(state.page.content_hash, hashlib.sha256(markdown).hexdigest())
            self.assertEqual(len(transport.requests), 5)
            self.assertNotIn("nothing pulled", output.getvalue())

    def test_force_prefers_remote_over_local_and_concurrent_changes(self) -> None:
        for version in [17, 18]:
            with self.subTest(version=version):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    directory = workarea.root_dir / "Example page"
                    (directory / "page.md").write_bytes(b"\xffinvalid markdown")
                    (directory / "_attachments/diagram.png").write_bytes(b"edited")
                    (directory / "_attachments/local.txt").write_text("unmanaged")
                    self._pull(workarea, page=self._page(version), force=True)

                    state = PageState.load(workarea.cache_path("123456"))
                    self.assertEqual((directory / "page.md").read_text(), "# Example page\n\nExample\n")
                    self.assertEqual((directory / "_attachments/diagram.png").read_bytes(), b"PNG")
                    self.assertEqual((directory / "_attachments/local.txt").read_text(), "unmanaged")
                    self.assertEqual(state.page.version, version)

    def test_force_restores_deleted_managed_files(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "page.md").unlink()
            (directory / "_attachments/diagram.png").unlink()
            self._pull(workarea, force=True)

            self.assertEqual((directory / "page.md").read_text(), "# Example page\n\nExample\n")
            self.assertEqual((directory / "_attachments/diagram.png").read_bytes(), b"PNG")

    def test_failed_force_pull_preserves_local_edits_and_cache(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/page.md").write_text("local edits")
            before = self._snapshot(workarea)
            with patch.object(PageState, "save", side_effect=SyncError("injected state failure")):
                with self.assertRaises(SyncError):
                    self._pull(workarea, force=True)

            self.assertEqual(self._snapshot(workarea), before)

    def test_local_and_both_sides_changes_conflict_without_mutation(self) -> None:
        for version in [17, 18]:
            for changed_file in ["page.md", "_attachments/diagram.png"]:
                with self.subTest(version=version, changed_file=changed_file):
                    with temporary_workarea() as workarea:
                        self._pull(workarea)
                        (workarea.root_dir / "Example page" / changed_file).write_text("edited\n")
                        before = self._snapshot(workarea)
                        with self.assertRaisesRegex(SyncError, "conflict"):
                            self._pull(workarea, page=self._page(version), downloads=[])

                        self.assertEqual(self._snapshot(workarea), before)

    def test_remote_update_renames_and_preserves_unmanaged_files(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            source = workarea.root_dir / "Example page"
            (source / "notes.txt").write_text("private notes")
            (source / "_attachments/local.txt").write_text("unmanaged")
            self._pull(workarea, page=self._page(18, "Renamed"), attachments=[])

            state = PageState.load(workarea.cache_path("123456"))
            target = workarea.page_directory(state)
            self.assertFalse(source.exists())
            self.assertEqual(state.page.directory, "Renamed")
            self.assertEqual(state.page.version, 18)
            self.assertEqual(state.attachments, {})
            self.assertFalse((target / "_attachments/diagram.png").exists())
            self.assertEqual((target / "notes.txt").read_text(), "private notes")
            self.assertEqual((target / "_attachments/local.txt").read_text(), "unmanaged")

    def test_attachment_version_change_without_page_change_is_pulled(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            attachment = attachment_fixture()
            attachment["version"] = {"number": 4}
            self._pull(workarea, attachments=[attachment], downloads=[MockResponse(200, {}, b"new")])

            state = PageState.load(workarea.cache_path("123456"))
            self.assertEqual(state.page.version, 17)
            self.assertEqual(state.attachments["diagram.png"].version, 4)
            self.assertEqual((workarea.page_directory(state) / "_attachments/diagram.png").read_bytes(), b"new")

    def test_title_and_unmanaged_attachment_collisions_leave_files_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Occupied").mkdir()
            before = self._snapshot(workarea)
            with self.assertRaises(SyncError):
                self._pull(workarea, page=self._page(18, "Occupied"))

            self.assertEqual(self._snapshot(workarea), before)
            (workarea.root_dir / "Example page/_attachments/local.txt").write_text("unmanaged")
            attachment = attachment_fixture()
            attachment["title"] = "local.txt"
            before = self._snapshot(workarea)
            with self.assertRaisesRegex(SyncError, "unmanaged"):
                self._pull(workarea, page=self._page(18), attachments=[attachment])

            self.assertEqual(self._snapshot(workarea), before)

    def test_download_and_conversion_failures_leave_previous_state_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            with self.assertRaises(SyncError):
                self._pull(workarea, page=self._page(18), downloads=[MockResponse(500, {}, b"failed")])

            self.assertEqual(self._snapshot(workarea), before)
            invalid = self._page(18)
            invalid["body"] = {"atlas_doc_format": {"value": "{"}}
            with self.assertRaises(SyncError):
                self._pull(workarea, page=invalid, downloads=[])

            self.assertEqual(self._snapshot(workarea), before)

    def test_failed_staging_write_leaves_previous_state_unchanged(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)
            write_bytes = Path.write_bytes

            def fail_write(path, data):
                if any(part.startswith(".cflsync-stage-") for part in path.parts):
                    raise OSError("injected staging write failure")

                return write_bytes(path, data)

            with patch.object(Path, "write_bytes", fail_write):
                with self.assertRaises(SyncError):
                    self._pull(workarea, page=self._page(18))

            self.assertEqual(self._snapshot(workarea), before)

    def test_missing_managed_attachment_conflicts(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            (workarea.root_dir / "Example page/_attachments/diagram.png").unlink()
            before = self._snapshot(workarea)
            with self.assertRaisesRegex(SyncError, "conflict"):
                self._pull(workarea, page=self._page(18), downloads=[])

            self.assertEqual(self._snapshot(workarea), before)

    def test_invalid_manifests_fail_before_download_or_mutation(self) -> None:
        unsafe = attachment_fixture()
        unsafe["title"] = "../outside"
        for manifest in [[unsafe], [attachment_fixture(), attachment_fixture()]]:
            with self.subTest(manifest=manifest):
                with temporary_workarea() as workarea:
                    before = self._snapshot(workarea)
                    with self.assertRaises(SyncError):
                        self._pull(workarea, attachments=manifest, downloads=[])

                    self.assertEqual(self._snapshot(workarea), before)

    def test_failed_install_or_state_write_rolls_back_first_pull_update_and_rename(self) -> None:
        for existing in [False, True]:
            for title in ["Example page", "Renamed"]:
                for failure in ["install", "state"]:
                    with self.subTest(existing=existing, title=title, failure=failure):
                        with temporary_workarea() as workarea:
                            if existing:
                                self._pull(workarea)

                            before = self._snapshot(workarea)
                            replace = os.replace

                            def fail_replace(source, target):
                                target = Path(target)
                                if failure == "state" and target == workarea.cache_path("123456"):
                                    raise OSError("injected state write failure")

                                if failure == "install" and any(part.startswith(".cflsync-stage-") for part in Path(source).parts):
                                    raise OSError("injected install failure")

                                return replace(source, target)

                            with patch("cflsync.workarea.os.replace", side_effect=fail_replace):
                                with self.assertRaises(SyncError):
                                    self._pull(workarea, page=self._page(18, title))

                            self.assertEqual(self._snapshot(workarea), before)


# vim: set ts=4 sw=4 et tw=132:
