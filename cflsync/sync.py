# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Change inspection shared by the synchronization commands."""

from __future__ import annotations

import hashlib
from pathlib import Path

from collections.abc import Iterator, Mapping

from .workarea import MediaResolver, PageState

ATTACHMENTS_PREFIX = "_attachments/"


class PageChanges:
    """What differs between local files, cached state, and remote metadata."""

    def __init__(
            self, page_locally: bool, attachments_locally: list[str], page_remotely: bool, attachments_remotely: list[str]) -> None:
        self.page_locally = page_locally
        self.attachments_locally = attachments_locally
        self.page_remotely = page_remotely
        self.attachments_remotely = attachments_remotely

    @property
    def locally(self) -> bool:
        """Report whether the page or any managed attachment changed locally."""
        return self.page_locally or bool(self.attachments_locally)

    @property
    def remotely(self) -> bool:
        """Report whether the page or its attachment manifest changed remotely."""
        return self.page_remotely or bool(self.attachments_remotely)


class PageInspector:
    """Compare a page's local files and remote metadata with its cached state."""

    def __init__(self, pandoc) -> None:
        self._pandoc_runner = pandoc

    def content_hash(self, markdown: str) -> str:
        """Return the format-1 content hash of *markdown*, canonicalized through Pandoc."""
        canonical = self._pandoc_runner.pandoc_to_gfm(self._pandoc_runner.gfm_to_pandoc(markdown))

        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def referenced_attachments(self, markdown: str) -> list[str]:
        """Return the managed attachment filenames that *markdown* links to."""
        names = []
        for target in _link_targets(self._pandoc_runner.gfm_to_pandoc(markdown)):
            if not target.startswith(ATTACHMENTS_PREFIX):
                continue

            name = target[len(ATTACHMENTS_PREFIX):]
            if name and name not in {".", ".."} and "/" not in name and "\\" not in name:
                names.append(name)

        return names

    def inspect(self, directory: Path, state: PageState, page, attachments) -> PageChanges:
        """Report local and remote changes for one cached page."""
        path = directory / "page.md"
        markdown = path.read_text(encoding="utf-8") if path.is_file() else None

        return PageChanges(
            markdown is None or self.content_hash(markdown) != state.page.content_hash,
            self._attachments_changed_locally(directory, state, markdown), self._page_changed_remotely(page, state),
            self._attachments_changed_remotely(attachments, state))

    def _attachments_changed_locally(self, directory, state, markdown):
        MediaResolver((name, attachment.id) for name, attachment in state.attachments.items())
        changed = set()
        for name, attachment in state.attachments.items():
            path = directory / "_attachments" / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != attachment.content_hash:
                changed.add(name)

        # A referenced local file becomes managed, so page.md can introduce attachments.
        for name in self.referenced_attachments(markdown or ""):
            if name not in state.attachments and (directory / "_attachments" / name).is_file():
                changed.add(name)

        return sorted(changed)

    def _page_changed_remotely(self, page, state):
        return page.version != state.page.version or page.title != state.page.title

    def _attachments_changed_remotely(self, attachments, state):
        remote = {attachment.filename: (attachment.id, attachment.version) for attachment in attachments}
        cached = {name: (attachment.id, attachment.version) for name, attachment in state.attachments.items()}
        changed = []
        for name in sorted(set(remote) | set(cached)):
            if remote.get(name) != cached.get(name):
                changed.append(name)

        return changed


def _link_targets(value: object) -> Iterator[str]:
    """Yield the target of every Pandoc link and image in *value*."""
    if isinstance(value, Mapping):
        if value.get("t") in {"Image", "Link"}:
            content = value.get("c")
            if isinstance(content, list) and len(content) == 3 and isinstance(content[2], list) and content[2]:
                target = content[2][0]
                if isinstance(target, str):
                    yield target

        value = list(value.values())

    if isinstance(value, list):
        for item in value:
            yield from _link_targets(item)


# vim: set ts=4 sw=4 et tw=132:
