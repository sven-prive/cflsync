# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Push decisions, uploads, and partial-failure behavior."""

from contextlib import redirect_stdout
import hashlib
from io import StringIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cflsync import APIClient, PageState, Profile, SyncError
from cflsync.cli import PagePullCommand, PagePushCommand
from tests.support import MockResponse, MockTransport, temporary_workarea
from tests.test_api_operations import attachment_fixture, page_fixture, user_fixture

PULLED_MARKDOWN = "# Example page\n\nExample\n"


def adf_body(text="Example"):
    document = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}

    return {"atlas_doc_format": {"value": json.dumps(document)}}


class TestPagePush(unittest.TestCase):

    def _page(self, version=17, title="Example page"):
        page = page_fixture(title=title)
        page["version"] = {"number": version}
        page["body"] = adf_body()

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

    def _pull(self, workarea, attachments=None):
        if attachments is None:
            attachments = [attachment_fixture()]

        page = self._page()
        responses = [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": attachments}), *[MockResponse(200, {}, b"PNG") for attachment in attachments], ]
        self._run(workarea, lambda: PagePullCommand().run("123456"), responses)

    def _push(self, workarea, responses, force=False):
        return self._run(workarea, lambda: PagePushCommand().run("123456", force=force), responses)

    def _push_responses(self, page=None, attachments=None, uploads=(), updated=None, after=None):
        """Queue resolve, fetch, manifest, uploads, refreshed manifest, and page update."""
        if page is None:
            page = self._page()

        if attachments is None:
            attachments = [attachment_fixture()]

        if after is None:
            after = attachments

        if updated is None:
            updated = self._page(version=page["version"]["number"] + 1)

        return [
            MockResponse.from_json(page),
            MockResponse.from_json(page),
            MockResponse.from_json({"results": attachments}), *uploads,
            MockResponse.from_json({"results": after}),
            MockResponse.from_json(updated), ]

    def _edit(self, workarea, markdown="# Example page\n\nEdited\n"):
        (workarea.root_dir / "Example page/page.md").write_text(markdown, encoding="utf-8")

    def _snapshot(self, workarea):
        return {
            str(path.relative_to(workarea.root_dir)): path.read_bytes() if path.is_file() else None
            for path in workarea.root_dir.rglob("*")}

    def test_unchanged_is_a_noop(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            before = self._snapshot(workarea)

            output, status, transport = self._push(
                workarea, [
                    MockResponse.from_json(self._page()),
                    MockResponse.from_json(self._page()),
                    MockResponse.from_json({"results": [attachment_fixture()]}), ])

            self.assertEqual(status, 0)
            self.assertIn("already in sync; nothing pushed", output)
            self.assertEqual(self._snapshot(workarea), before)
            self.assertTrue(all(request.method == "GET" for request in transport.requests))

    def test_remote_and_both_side_changes_conflict(self) -> None:
        for local_edit in [False, True]:
            with self.subTest(local_edit=local_edit):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    if local_edit:
                        self._edit(workarea)

                    before = self._snapshot(workarea)
                    remote = self._page(version=18)
                    responses = [
                        MockResponse.from_json(remote),
                        MockResponse.from_json(remote),
                        MockResponse.from_json({"results": [attachment_fixture()]}), ]

                    with self.assertRaisesRegex(SyncError, "push conflicts"):
                        self._push(workarea, responses)

                    self.assertEqual(self._snapshot(workarea), before)

    def test_uploads_local_page_changes_and_commits_state(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            markdown = "# Example page\n\nEdited\n"
            page_path = workarea.root_dir / "Example page/page.md"
            # Exercise an ordinary Windows editor save on every platform.
            page_path.write_text(markdown, encoding="utf-8", newline="\r\n")

            _, status, transport = self._push(workarea, self._push_responses())

            update = transport.requests[-1]
            document = json.loads(json.loads(update.body)["body"]["value"])
            state = PageState.load(workarea.cache_path("123456"))
            self.assertEqual(status, 0)
            self.assertEqual(update.method, "PUT")
            self.assertEqual(update.path, "/pages/123456")
            self.assertEqual(json.loads(update.body)["version"], {"number": 18})
            # The title heading belongs to the page, not to its body.
            self.assertEqual(document["content"][0]["content"][0]["text"], "Edited")
            self.assertEqual(state.page.version, 18)
            # Format 1 hashes canonical GFM, rather than the platform-specific
            # bytes used to store the editable Markdown file.
            self.assertEqual(state.page.content_hash, hashlib.sha256(markdown.encode("utf-8")).hexdigest())

    def test_resolves_a_mailto_link_to_a_mention_when_pushing(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            self._edit(workarea, "# Example page\n\n[Example User](mailto:example.user@example.test)\n")
            responses = self._push_responses()
            responses.insert(-1, MockResponse.from_json({"results": [{"user": user_fixture()}]}))

            _, status, transport = self._push(workarea, responses)

            document = json.loads(json.loads(transport.requests[-1].body)["body"]["value"])
            lookup = transport.requests[-2]
            self.assertEqual(status, 0)
            self.assertEqual(lookup.path, "/search/user")
            self.assertEqual(lookup.parameters, {"cql": 'user.fullname~"Example User"'})
            self.assertEqual(
                document["content"][0]["content"], [{
                    "type": "mention",
                    "attrs": {
                        "id": "account-123",
                        "text": "@Example User"}}])

    def test_uploads_changed_and_added_attachments(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "_attachments/diagram.png").write_bytes(b"EDITED")
            (directory / "_attachments/added.png").write_bytes(b"ADDED")
            (directory / "_attachments/ignored.png").write_bytes(b"IGNORED")
            self._edit(workarea, f"{PULLED_MARKDOWN}\n![Added](_attachments/added.png)\n")

            added = attachment_fixture()
            added["id"], added["title"], added["fileId"] = "att111111", "added.png", "file-added"
            uploads = [MockResponse.from_json({"results": [added]}), MockResponse.from_json(attachment_fixture())]

            _, _, transport = self._push(workarea, self._push_responses(uploads=uploads, after=[attachment_fixture(), added]))

            writes = [(request.method, request.path) for request in transport.requests if request.method in {"POST", "PUT"}]
            state = PageState.load(workarea.cache_path("123456"))
            self.assertIn(("PUT", "/content/123456/child/attachment"), writes)
            self.assertIn(("POST", "/content/123456/child/attachment/att567890/data"), writes)
            self.assertEqual(sorted(state.attachments), ["added.png", "diagram.png"])
            self.assertEqual(state.attachments["added.png"].content_hash, hashlib.sha256(b"ADDED").hexdigest())
            self.assertEqual(state.attachments["diagram.png"].content_hash, hashlib.sha256(b"EDITED").hexdigest())

    def test_deletes_only_previously_managed_removed_attachments(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            directory = workarea.root_dir / "Example page"
            (directory / "_attachments/diagram.png").unlink()
            (directory / "_attachments/unmanaged.png").write_bytes(b"KEEP")
            self._edit(workarea)

            _, _, transport = self._push(workarea, self._push_responses() + [MockResponse(204, {}, b"")])

            deletes = [request.path for request in transport.requests if request.method == "DELETE"]
            state = PageState.load(workarea.cache_path("123456"))
            self.assertEqual(deletes, ["/attachments/att567890"])
            self.assertEqual(dict(state.attachments), {})
            self.assertTrue((directory / "_attachments/unmanaged.png").is_file())

    def test_rejects_an_edited_title_heading(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            self._edit(workarea, "# Renamed page\n\nExample\n")

            with self.assertRaisesRegex(SyncError, "renaming is not supported"):
                self._push(workarea, self._push_responses())

            self.assertEqual(PageState.load(workarea.cache_path("123456")).page.version, 17)

    def test_version_mismatch_during_update_is_a_conflict(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            self._edit(workarea)
            before = self._snapshot(workarea)
            responses = self._push_responses()[:-1] + [MockResponse.from_json({"message": "version conflict"}, 409)]

            with self.assertRaises(SyncError):
                self._push(workarea, responses)

            self.assertEqual(self._snapshot(workarea), before)

    def test_failed_upload_or_delete_leaves_state_unchanged(self) -> None:
        cases = [
            ("upload", [MockResponse.from_json({"message": "upload rejected"}, 500)], []),
            ("delete", [], [MockResponse.from_json({"message": "delete rejected"}, 500)]), ]
        for name, uploads, deletes in cases:
            with self.subTest(failure=name):
                with temporary_workarea() as workarea:
                    self._pull(workarea)
                    directory = workarea.root_dir / "Example page"
                    if name == "upload":
                        (directory / "_attachments/diagram.png").write_bytes(b"EDITED")
                    else:
                        (directory / "_attachments/diagram.png").unlink()

                    self._edit(workarea)
                    before = self._snapshot(workarea)

                    with self.assertRaises(SyncError):
                        self._push(workarea, self._push_responses(uploads=uploads) + deletes)

                    self.assertEqual(self._snapshot(workarea), before)
                    self.assertEqual(PageState.load(workarea.cache_path("123456")).page.version, 17)

    def test_force_pushes_over_remote_changes(self) -> None:
        with temporary_workarea() as workarea:
            self._pull(workarea)
            self._edit(workarea)
            remote = self._page(version=18)
            responses = self._push_responses(page=remote, updated=self._page(version=19))

            _, status, transport = self._push(workarea, responses, force=True)

            state = PageState.load(workarea.cache_path("123456"))
            self.assertEqual(status, 0)
            self.assertEqual(state.page.version, 19)
            self.assertEqual(json.loads(transport.requests[-1].body)["version"], {"number": 19})

    def test_rejects_a_page_without_a_cache_entry(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaisesRegex(SyncError, "not managed"):
                self._push(workarea, [MockResponse.from_json(self._page())])


# vim: set ts=4 sw=4 et tw=132:
