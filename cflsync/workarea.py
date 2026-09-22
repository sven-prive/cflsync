#! /usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Delay annotation evaluation so nested type references work on Python 3.11+.
from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from tempfile import NamedTemporaryFile

from .errors import SyncError


class MediaResolutionError(SyncError):
    """Raised when a managed attachment cannot be resolved safely."""


class MediaResolver:
    """Map a page attachment manifest between IDs and local paths."""

    def __init__(self, manifest: Iterable[tuple[str, str]]) -> None:
        paths_by_id = {}
        ids_by_filename = {}
        for filename, attachment_id in manifest:
            self._validate_filename(filename)
            self._validate_attachment_id(attachment_id)
            if filename in ids_by_filename:
                raise MediaResolutionError(f"attachment filename '{filename}' is ambiguous")

            if attachment_id in paths_by_id:
                raise MediaResolutionError(f"attachment ID '{attachment_id}' is ambiguous")

            paths_by_id[attachment_id] = f"_attachments/{filename}"
            ids_by_filename[filename] = attachment_id

        self._paths_by_id = paths_by_id
        self._ids_by_filename = ids_by_filename

    def path_for(self, attachment_id: str) -> str:
        """Return the managed Markdown path for one attachment ID."""
        self._validate_attachment_id(attachment_id)
        try:
            return self._paths_by_id[attachment_id]
        except KeyError as error:
            raise MediaResolutionError(f"attachment ID '{attachment_id}' is not managed") from error

    def id_for(self, path: str) -> str:
        """Return the attachment ID for one managed Markdown path."""
        filename = self._filename_from_path(path)
        try:
            return self._ids_by_filename[filename]
        except KeyError as error:
            raise MediaResolutionError(f"attachment path '{path}' is not managed") from error

    def _validate_filename(self, filename):
        if not isinstance(filename, str) or not filename or filename in {".", ".."}:
            raise MediaResolutionError("attachment filename must be a non-empty basename")

        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise MediaResolutionError(f"attachment filename '{filename}' is unsafe")

    def _validate_attachment_id(self, attachment_id):
        if not isinstance(attachment_id, str) or not attachment_id:
            raise MediaResolutionError("attachment ID must be a non-empty string")

    def _filename_from_path(self, path):
        if not isinstance(path, str):
            raise MediaResolutionError("attachment path must be a string")

        parts = path.split("/")
        if len(parts) != 2 or parts[0] != "_attachments":
            raise MediaResolutionError(f"attachment path '{path}' is outside _attachments")

        filename = parts[1]
        self._validate_filename(filename)

        return filename


class StateError(SyncError):
    """Raised when a page synchronization state file is invalid."""


class PageMetadata:
    """The last synchronized state of one Confluence page."""

    def __init__(self, id: str, title: str, directory: str, version: int, content_hash: str) -> None:
        if not id or not id.isdigit():
            raise StateError("page.id must be a numeric identifier")
        if not title:
            raise StateError("page.title must be a non-empty string")
        if not directory:
            raise StateError("page.directory must be a non-empty string")
        if version < 1:
            raise StateError("page.version must be a positive integer")
        if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise StateError("page.content_hash must be a SHA-256 hexadecimal digest")

        self.id = id
        self.title = title
        self.directory = directory
        self.version = version
        self.content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PageMetadata):
            return NotImplemented

        return (self.id, self.title, self.directory, self.version,
                self.content_hash) == (other.id, other.title, other.directory, other.version, other.content_hash)

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "directory": self.directory,
            "version": self.version,
            "content_hash": self.content_hash}

    @classmethod
    def from_json(cls, value: object) -> "PageMetadata":
        """Validate and decode page metadata from a state JSON value."""
        if not isinstance(value, Mapping):
            raise StateError("page must be an object")

        try:
            id = value["id"]
            title = value["title"]
            directory = value["directory"]
            version = value["version"]
            content_hash = value["content_hash"]
        except KeyError as error:
            raise StateError(f"page.{error.args[0]} is required") from error
        if not isinstance(id, str):
            raise StateError("page.id must be a string")
        if not isinstance(title, str):
            raise StateError("page.title must be a string")
        if not isinstance(directory, str):
            raise StateError("page.directory must be a string")
        if type(version) is not int:
            raise StateError("page.version must be an integer")
        if not isinstance(content_hash, str):
            raise StateError("page.content_hash must be a string")

        return cls(id, title, directory, version, content_hash)


class AttachmentMetadata:
    """The last synchronized state of one managed attachment."""

    def __init__(self, id: str, version: int, content_hash: str) -> None:
        if not id or not id.isdigit():
            raise StateError("attachment.id must be a numeric identifier")
        if version < 1:
            raise StateError("attachment.version must be a positive integer")
        if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise StateError("attachment.content_hash must be a SHA-256 hexadecimal digest")

        self.id = id
        self.version = version
        self.content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AttachmentMetadata):
            return NotImplemented

        return (self.id, self.version, self.content_hash) == (other.id, other.version, other.content_hash)

    def to_json(self) -> dict[str, object]:
        return {"id": self.id, "version": self.version, "content_hash": self.content_hash}

    @classmethod
    def from_json(cls, value: object, name: str) -> "AttachmentMetadata":
        """Validate and decode attachment metadata from a state JSON value."""
        if not isinstance(value, Mapping):
            raise StateError(f"attachment '{name}' must be an object")
        try:
            id = value["id"]
            version = value["version"]
            content_hash = value["content_hash"]
        except KeyError as error:
            raise StateError(f"attachment '{name}'.{error.args[0]} is required") from error
        if not isinstance(id, str):
            raise StateError(f"attachment '{name}'.id must be a string")
        if type(version) is not int:
            raise StateError(f"attachment '{name}'.version must be an integer")
        if not isinstance(content_hash, str):
            raise StateError(f"attachment '{name}'.content_hash must be a string")

        return cls(id, version, content_hash)


class PageState:
    """Format-1 synchronization state for one managed page."""

    def __init__(self, page: PageMetadata, attachments: Mapping[str, AttachmentMetadata], format: int = 1) -> None:
        if format != 1:
            raise StateError("unsupported state format")

        copied_attachments: dict[str, AttachmentMetadata] = {}
        for name, attachment in attachments.items():
            if not name:
                raise StateError("attachment name must be a non-empty string")
            copied_attachments[name] = attachment

        self.page = page
        self.attachments = copied_attachments
        self.format = format

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PageState):
            return NotImplemented

        return (self.page, self.attachments, self.format) == (other.page, other.attachments, other.format)

    def to_json(self) -> dict[str, object]:
        return {
            "format": self.format,
            "page": self.page.to_json(),
            "attachments": {
                name: attachment.to_json()
                for name, attachment in self.attachments.items()}}

    @classmethod
    def from_json(cls, value: object) -> "PageState":
        """Validate and decode a format-1 state JSON value."""
        if not isinstance(value, Mapping):
            raise StateError("state must be an object")
        try:
            state_format = value["format"]
            page_value = value["page"]
            attachment_values = value["attachments"]
        except KeyError as error:
            raise StateError(f"state.{error.args[0]} is required") from error
        if type(state_format) is not int:
            raise StateError("state.format must be an integer")
        if not isinstance(attachment_values, Mapping):
            raise StateError("attachments must be an object")

        attachments: dict[str, AttachmentMetadata] = {}
        for name, attachment_value in attachment_values.items():
            if not isinstance(name, str) or not name:
                raise StateError("attachment name must be a non-empty string")
            attachments[name] = AttachmentMetadata.from_json(attachment_value, name)

        return cls(page=PageMetadata.from_json(page_value), attachments=attachments, format=state_format)

    @classmethod
    def load(cls, workarea: "Workarea", page_id: str) -> "PageState":
        """Load the validated state file whose cache key is *page_id*."""
        try:
            path = workarea.cache_path(page_id)
        except ValueError as error:
            raise StateError(str(error)) from error

        try:
            with path.open(encoding="utf-8") as state_file:
                value = json.load(state_file)
        except FileNotFoundError as error:
            raise StateError(f"state file does not exist for page '{page_id}'") from error
        except json.JSONDecodeError as error:
            raise StateError(f"invalid JSON in state file for page '{page_id}'") from error
        except OSError as error:
            raise StateError(f"cannot read state file for page '{page_id}': {error}") from error

        state = cls.from_json(value)
        if state.page.id != page_id:
            raise StateError(f"state file '{path.name}' does not match page.id '{state.page.id}'")

        return state

    def save(self, workarea: "Workarea") -> None:
        """Atomically persist this state under its page-ID cache key."""
        try:
            path = workarea.cache_path(self.page.id)
        except ValueError as error:
            raise StateError(str(error)) from error

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{self.page.id}.", suffix=".tmp",
                                    delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_path.chmod(0o600)
                json.dump(self.to_json(), temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, path)
        except OSError as error:
            raise StateError(f"cannot write state file for page '{self.page.id}': {error}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass


class Workarea:

    class Error(SyncError):

        def __init__(self, message):
            super().__init__(message)

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()

    @classmethod
    def init(cls, p: Path, profile: str = "default"):
        """Initialise an empty workarea at path p using the named auth profile."""
        if not p.is_dir():
            raise Workarea.Error(f"'{p}' is not a directory")
        if not profile or "\n" in profile or "\r" in profile:
            raise Workarea.Error("profile must be a non-empty single-line name")

        try:
            wa = cls.find(p)
        except Workarea.Error:
            wa = None

        if wa is not None:
            raise Workarea.Error(f"'{p}' is already part of a cflsync workarea ({wa.root_dir})")

        cflsync_dir = p / ".cflsync"
        if cflsync_dir.exists():
            raise Workarea.Error(f"'{p}' already contains '{cflsync_dir.name}'")

        cache_dir = cflsync_dir / "cache"
        cflsync_dir.mkdir(mode=0o700)
        cflsync_dir.chmod(0o700)
        cache_dir.mkdir(mode=0o700)
        cache_dir.chmod(0o700)

        profile_path = cflsync_dir / "profile"
        with open(profile_path, "w") as f:
            f.write(f"{profile}\n")
        profile_path.chmod(0o600)

        return cls(p)

    @property
    def cflsync_dir(self) -> Path:
        return self.root_dir / ".cflsync"

    @property
    def cache_dir(self) -> Path:
        return self.cflsync_dir / "cache"

    @property
    def profile(self) -> str:
        with open(self.cflsync_dir / "profile", "r") as f:
            return f.read().rstrip("\r\n")

    def cache_path(self, page_id: str) -> Path:
        if not page_id.isdigit():
            raise ValueError(f"expected page-id, got '{page_id}'")

        return self.cache_dir / f"{page_id}.json"

    def page_states(self) -> dict[str, PageState]:
        """Return every validated page state, ordered by numeric page ID."""
        try:
            cache_paths = sorted(self.cache_dir.glob("*.json"), key=lambda path: int(path.stem))
        except ValueError as error:
            raise StateError("cache contains a non-numeric page-state filename") from error

        states: dict[str, PageState] = {}
        directories: dict[str, str] = {}
        for cache_path in cache_paths:
            if not cache_path.is_file():
                raise StateError(f"cache entry '{cache_path.name}' is not a file")
            state = PageState.load(self, cache_path.stem)
            assigned_page_id = directories.get(state.page.directory)
            if assigned_page_id is not None:
                raise Workarea.Error(
                    f"page directory '{state.page.directory}' is assigned to both '{assigned_page_id}' and '{state.page.id}'")
            directories[state.page.directory] = state.page.id
            states[state.page.id] = state

        return states

    def page_state(self, page_id: str) -> PageState:
        """Return the validated cached state for *page_id*."""
        return PageState.load(self, page_id)

    def page_directory(self, state: PageState) -> Path:
        """Return the existing managed directory recorded in *state*."""
        directory = self._page_directory_path(state.page.directory)
        if not directory.is_dir():
            raise Workarea.Error("managed page directory does not exist")
        if not (directory / "page.md").is_file():
            raise Workarea.Error("managed page directory does not contain page.md")

        return directory

    def page_directory_target(self, state: PageState) -> Path:
        """Return a safe, unoccupied target path for a page directory."""
        directory = self._page_directory_path(state.page.directory)
        cached_state = self.page_states().get(state.page.id)
        if directory.exists() and (cached_state is None or cached_state.page.directory != state.page.directory):
            raise Workarea.Error(f"page directory '{state.page.directory}' already exists")

        return directory

    def _page_directory_path(self, directory_name: str) -> Path:
        directory = Path(directory_name)
        if directory.is_absolute() or directory.name != directory_name or directory_name in {".", ".."}:
            raise Workarea.Error("page directory must be a single relative name")

        path = (self.root_dir / directory).resolve()
        try:
            path.relative_to(self.root_dir)
        except ValueError as error:
            raise Workarea.Error("page directory is outside the workarea") from error
        if path == self.root_dir:
            raise Workarea.Error("page directory must be below the workarea root")

        return path

    @classmethod
    def find(cls, p: Path):
        """Locate the workarea, if any, that contains path p."""
        dir = (p if p.is_dir() else p.parent).resolve()
        while True:
            cflsync_dir = dir / ".cflsync"
            if cflsync_dir.is_dir() and (cflsync_dir / "profile").is_file():
                return cls(dir)

            if dir == dir.parent:
                raise Workarea.Error(f"'{p}' is not part of a cflsync workarea")

            dir = dir.parent


class PageRefError(SyncError):
    """Raised when a page reference cannot identify exactly one page."""


class PageRef:
    """A resolved Confluence page identifier."""

    def __init__(self, page_id: str) -> None:
        self.page_id = page_id

    @classmethod
    def resolve(cls, value: str | Path, workarea: Workarea, api, cwd: Path | None = None) -> "PageRef":
        """Resolve a local path, page ID, or title to one Confluence page ID."""
        text = str(value)
        path = _page_ref_path(value, cwd)
        if path.exists():
            return cls._from_path(path, workarea)
        if text.isdigit():
            return cls(api.get_page(text).id)

        states = workarea.page_states()
        cached_ids = [state.page.id for state in states.values() if state.page.title == text]
        if cached_ids:
            return cls(_one_page_ref_id(cached_ids, f"cached title '{text}'"))

        pages = [page for page in api.find_pages_by_title(text) if page.title == text]
        return cls(_one_page_ref_id([page.id for page in pages], f"title '{text}'"))

    @classmethod
    def _from_path(cls, path: Path, workarea: Workarea) -> "PageRef":
        try:
            relative_path = path.relative_to(workarea.root_dir)
        except ValueError as error:
            raise PageRefError(f"page path '{path}' is outside the workarea") from error

        if path.is_file():
            if path.name != "page.md":
                raise PageRefError(f"page file '{path}' is not named page.md")
            directory = path.parent
            relative_path = relative_path.parent
        elif path.is_dir():
            directory = path
            if not (directory / "page.md").is_file():
                raise PageRefError(f"page directory '{path}' does not contain page.md")
        else:
            raise PageRefError(f"page path '{path}' is neither a file nor a directory")

        if len(relative_path.parts) != 1:
            raise PageRefError(f"page path '{path}' is not a managed page directory")
        directory_name = relative_path.name
        states = workarea.page_states()
        page_id = next((state.page.id for state in states.values() if state.page.directory == directory_name), None)
        if page_id is None:
            raise PageRefError(f"page path '{path}' is not managed by cflsync")

        state = states[page_id]
        if workarea.page_directory(state) != directory:
            raise PageRefError(f"page path '{path}' does not match its cached page state")

        return cls(page_id)


def _page_ref_path(value: str | Path, cwd: Path | None) -> Path:
    base = cwd or Path.cwd()
    return (base / Path(value)).resolve()


def _one_page_ref_id(page_ids: list[str], description: str) -> str:
    if not page_ids:
        raise PageRefError(f"no page matches {description}")
    if len(page_ids) > 1:
        raise PageRefError(f"multiple pages match {description}: {', '.join(page_ids)}")

    return page_ids[0]


# vim: set ts=4 sw=4 et tw=132:
