# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Local and remote change inspection against a cached page state."""

import hashlib
from types import SimpleNamespace
import unittest

from cflsync import AttachmentMetadata, PageInspector, PageMetadata, PageState, PandocRunner
from tests.support import temporary_workarea

MARKDOWN = "# Example page\n\nExample\n"
ATTACHMENT = b"PNG"


def remote_page(version=17, title="Example page"):
    return SimpleNamespace(id="123456", title=title, version=version)


def remote_attachment(filename="diagram.png", attachment_id="att987654", version=3):
    return SimpleNamespace(filename=filename, id=attachment_id, version=version)


class TestPageInspector(unittest.TestCase):

    def setUp(self) -> None:
        self.inspector = PageInspector(PandocRunner())

    def _page(self, workarea):
        """Install a page whose local files, cache, and remote metadata all agree."""
        state = PageState(
            PageMetadata("123456", "Example page", "Example page", 17, self.inspector.content_hash(MARKDOWN)),
            {"diagram.png": AttachmentMetadata("att987654", 3,
                                               hashlib.sha256(ATTACHMENT).hexdigest())})
        directory = workarea.root_dir / "Example page"
        (directory / "_attachments").mkdir(parents=True)
        (directory / "page.md").write_text(MARKDOWN, encoding="utf-8")
        (directory / "_attachments/diagram.png").write_bytes(ATTACHMENT)

        return directory, state

    def _inspect(self, directory, state, page=None, attachments=None):
        if page is None:
            page = remote_page()

        if attachments is None:
            attachments = [remote_attachment()]

        return self.inspector.inspect(directory, state, page, attachments)

    def test_hashes_formatting_only_differences_identically(self) -> None:
        equivalent = "# Example page\n\n\nExample\n\n"

        self.assertEqual(self.inspector.content_hash(MARKDOWN), self.inspector.content_hash(equivalent))
        self.assertNotEqual(self.inspector.content_hash(MARKDOWN), self.inspector.content_hash("# Example page\n\nEdited\n"))

    def test_reports_no_change_when_all_three_agree(self) -> None:
        with temporary_workarea() as workarea:
            changes = self._inspect(*self._page(workarea))

            self.assertEqual((changes.locally, changes.remotely), (False, False))
            self.assertEqual((changes.attachments_locally, changes.attachments_remotely), ([], []))

    def test_reports_local_page_edits_and_removal(self) -> None:
        for content in ["# Example page\n\nEdited\n", None]:
            with self.subTest(content=content):
                with temporary_workarea() as workarea:
                    directory, state = self._page(workarea)
                    if content is None:
                        (directory / "page.md").unlink()
                    else:
                        (directory / "page.md").write_text(content, encoding="utf-8")

                    changes = self._inspect(directory, state)

                    self.assertTrue(changes.page_locally)
                    self.assertTrue(changes.locally)
                    self.assertFalse(changes.remotely)

    def test_reports_changed_and_missing_managed_attachments_by_name(self) -> None:
        for edit in ["bytes", "remove"]:
            with self.subTest(edit=edit):
                with temporary_workarea() as workarea:
                    directory, state = self._page(workarea)
                    path = directory / "_attachments/diagram.png"
                    if edit == "bytes":
                        path.write_bytes(b"edited")
                    else:
                        path.unlink()

                    changes = self._inspect(directory, state)

                    self.assertEqual(changes.attachments_locally, ["diagram.png"])
                    self.assertFalse(changes.page_locally)

    def test_ignores_unmanaged_local_files(self) -> None:
        with temporary_workarea() as workarea:
            directory, state = self._page(workarea)
            (directory / "_attachments/notes.txt").write_text("unmanaged")
            (directory / "scratch.md").write_text("unmanaged")

            changes = self._inspect(directory, state)

            self.assertFalse(changes.locally)

    def test_reports_remote_page_version_and_title_changes(self) -> None:
        for page in [remote_page(version=18), remote_page(title="Renamed page")]:
            with self.subTest(version=page.version, title=page.title):
                with temporary_workarea() as workarea:
                    changes = self._inspect(*self._page(workarea), page=page)

                    self.assertTrue(changes.page_remotely)
                    self.assertTrue(changes.remotely)
                    self.assertFalse(changes.locally)

    def test_reports_remote_attachment_updates_deletions_and_additions(self) -> None:
        cases = [
            ("updated", [remote_attachment(version=4)], ["diagram.png"]),
            ("replaced", [remote_attachment(attachment_id="att111111")], ["diagram.png"]), ("deleted", [], ["diagram.png"]),
            ("added", [remote_attachment(), remote_attachment("report.pdf", "att222222", 1)], ["report.pdf"]), ]
        for name, attachments, expected in cases:
            with self.subTest(case=name):
                with temporary_workarea() as workarea:
                    changes = self._inspect(*self._page(workarea), attachments=attachments)

                    self.assertEqual(changes.attachments_remotely, expected)
                    self.assertFalse(changes.page_remotely)
                    self.assertFalse(changes.locally)

    def test_reports_both_sides_when_each_changed(self) -> None:
        with temporary_workarea() as workarea:
            directory, state = self._page(workarea)
            (directory / "page.md").write_text("# Example page\n\nEdited\n", encoding="utf-8")

            changes = self._inspect(directory, state, page=remote_page(version=18))

            self.assertEqual((changes.locally, changes.remotely), (True, True))


# vim: set ts=4 sw=4 et tw=132:
