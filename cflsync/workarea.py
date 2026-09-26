# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Delay annotation evaluation so nested type references work on Python 3.11+.
from __future__ import annotations

import errno
import json
import os
import re
import shutil
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile, mkdtemp
from typing import Self
from urllib.parse import quote

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
    """The last synchronized state of one Confluence page.

    *parent_id* is the cached parent page, whose directory contains this page's directory, or ``None`` for the root
    page. *directory* is this page's own directory name, relative to its parent's directory.
    """

    def __init__(self, id: str, title: str, parent_id: str | None, directory: str, version: int, content_hash: str) -> None:
        if not id or not id.isdigit():
            raise StateError("page.id must be a numeric identifier")
        if not title:
            raise StateError("page.title must be a non-empty string")
        if parent_id is not None and (not parent_id or not parent_id.isdigit()):
            raise StateError("page.parent_id must be a numeric identifier or null")
        if not directory:
            raise StateError("page.directory must be a non-empty string")
        if directory in {".", ".."} or "/" in directory or "\\" in directory or "\x00" in directory:
            raise StateError("page.directory must be a single directory name")
        if version < 1:
            raise StateError("page.version must be a positive integer")
        if re.fullmatch(r"[0-9a-f]{64}", content_hash) is None:
            raise StateError("page.content_hash must be a SHA-256 hexadecimal digest")

        self.id = id
        self.title = title
        self.parent_id = parent_id
        self.directory = directory
        self.version = version
        self.content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PageMetadata):
            return NotImplemented

        return (self.id, self.title, self.parent_id, self.directory, self.version,
                self.content_hash) == (other.id, other.title, other.parent_id, other.directory, other.version, other.content_hash)

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "parent_id": self.parent_id,
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
            parent_id = value["parent_id"]
            directory = value["directory"]
            version = value["version"]
            content_hash = value["content_hash"]
        except KeyError as error:
            raise StateError(f"page.{error.args[0]} is required") from error
        if not isinstance(id, str):
            raise StateError("page.id must be a string")
        if not isinstance(title, str):
            raise StateError("page.title must be a string")
        if parent_id is not None and not isinstance(parent_id, str):
            raise StateError("page.parent_id must be a string or null")
        if not isinstance(directory, str):
            raise StateError("page.directory must be a string")
        if type(version) is not int:
            raise StateError("page.version must be an integer")
        if not isinstance(content_hash, str):
            raise StateError("page.content_hash must be a string")

        return cls(id, title, parent_id, directory, version, content_hash)


class AttachmentMetadata:
    """The last synchronized state of one managed attachment."""

    def __init__(self, id: str, version: int, content_hash: str) -> None:
        if not id or re.search(r"[\s/\\\x00]", id):
            raise StateError("attachment.id must be a non-empty opaque identifier")
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
    """Format-2 synchronization state for one managed page."""

    def __init__(self, page: PageMetadata, attachments: Mapping[str, AttachmentMetadata], format: int = 2) -> None:
        if format != 2:
            raise StateError(f"unsupported state format {format}")

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
        """Validate and decode a format-2 state JSON value."""
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
    def load(cls, path: Path) -> Self:
        """Load the validated state file at *path*."""
        if path.suffix != ".json" or not path.stem.isdigit():
            raise StateError(f"state path '{path}' must have a numeric cache filename")

        try:
            with path.open(encoding="utf-8") as state_file:
                value = json.load(state_file)
        except FileNotFoundError as error:
            raise StateError(f"state file does not exist for page '{path.stem}'") from error
        except json.JSONDecodeError as error:
            raise StateError(f"invalid JSON in state file for page '{path.stem}'") from error
        except OSError as error:
            raise StateError(f"cannot read state file for page '{path.stem}': {error}") from error

        state = cls.from_json(value)
        if state.page.id != path.stem:
            raise StateError(f"state file '{path.name}' does not match page.id '{state.page.id}'")

        return cls(state.page, state.attachments, state.format)

    def save(self, path: Path) -> None:
        """Atomically persist this state under its page-ID cache key."""
        if path.suffix != ".json" or path.stem != self.page.id:
            raise StateError(f"state path '{path}' does not match page.id '{self.page.id}'")

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{self.page.id}.", suffix=".tmp",
                                    delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                if not _is_windows():
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


class PageTree:
    """The cached pages of a workarea, with each page's directory derived from its chain of cached parents.

    Every cached page except the root page has a cached parent. A missing parent, a page other than the root without
    a parent, a root with a parent, or a cycle makes the cache invalid.
    """

    def __init__(self, states: Mapping[str, PageState], root_page_id: str) -> None:
        self.states = dict(states)
        self.root_page_id = root_page_id
        self._directories = {page_id: self._derive_directory(page_id) for page_id in self.states}

    def directory(self, page_id: str) -> str:
        """Return a cached page's directory relative to the workarea root, with "/" separators."""
        if page_id not in self._directories:
            raise StateError(f"page '{page_id}' is not cached")

        return self._directories[page_id]

    def _derive_directory(self, page_id):
        names = []
        seen = set()
        current = page_id
        while True:
            if current in seen:
                raise StateError(f"the cached parents of page '{page_id}' form a cycle")

            seen.add(current)
            state = self.states.get(current)
            if state is None:
                raise StateError(f"cached parent page '{current}' of page '{page_id}' is missing")

            names.insert(0, state.page.directory)
            parent_id = state.page.parent_id
            if current == self.root_page_id:
                if parent_id is not None:
                    raise StateError(f"root page '{current}' must not have a cached parent")

                return "/".join(names)

            if parent_id is None:
                raise StateError(f"cached page '{current}' has no parent but is not the root page '{self.root_page_id}'")

            current = parent_id


class Workarea:

    class Error(SyncError):

        def __init__(self, message):
            super().__init__(message)

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir.resolve()

    @classmethod
    def init(cls, p: Path, root_page_id: str, profile: str = "default"):
        """Initialise an empty workarea at path p, anchored at a root page and using the named auth profile."""
        if not p.is_dir():
            raise Workarea.Error(f"'{p}' is not a directory")
        if re.fullmatch(r"[0-9]+", root_page_id) is None:
            raise Workarea.Error("root page ID must be numeric")
        if not profile or "\n" in profile or "\r" in profile:
            raise Workarea.Error("profile must be a non-empty single-line name")

        # Any existing workarea counts, including a version-1 workarea that find() refuses.
        existing = cls._locate(p)
        if existing is not None:
            raise Workarea.Error(f"'{p}' is already part of a cflsync workarea ({existing})")

        cflsync_dir = p / ".cflsync"
        if cflsync_dir.exists():
            raise Workarea.Error(f"'{p}' already contains '{cflsync_dir.name}'")

        staging: Path | None = None
        try:
            staging = Path(mkdtemp(prefix=".cflsync-init-", dir=p))
            cache_dir = staging / "cache"
            cache_dir.mkdir(mode=0o700)
            if not _is_windows():
                cache_dir.chmod(0o700)

            for name, value in [("profile", profile), ("root", root_page_id)]:
                path = staging / name
                with path.open("w", encoding="utf-8") as file:
                    file.write(f"{value}\n")
                    file.flush()
                    os.fsync(file.fileno())
                if not _is_windows():
                    path.chmod(0o600)

            os.replace(staging, cflsync_dir)
        except OSError as error:
            raise Workarea.Error(f"cannot initialise workarea: {error}") from error
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)

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

    @property
    def root_page_id(self) -> str:
        """Return the ID of the root page that anchors this workarea."""
        path = self.cflsync_dir / "root"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as error:
            raise Workarea.Error(
                f"'{self.root_dir}' is a version-1 cflsync workarea, which this version of cflsync does not support; "
                "create a new workarea anchored at a root page with 'cflsync init ROOT_PAGE_REF'") from error
        except (OSError, UnicodeError) as error:
            raise Workarea.Error(f"cannot read '{path}': {filesystem_error_message(error)}") from error

        root_page_id = text.removesuffix("\n")
        if re.fullmatch(r"[0-9]+", root_page_id) is None:
            raise Workarea.Error(f"'{path}' must contain one numeric page ID")

        return root_page_id

    def cache_path(self, page_id: str) -> Path:
        if not page_id.isdigit():
            raise ValueError(f"expected page-id, got '{page_id}'")

        return self.cache_dir / f"{page_id}.json"

    def page_state_paths(self) -> dict[str, Path]:
        """Return every numeric page-ID cache path, ordered by page ID."""
        try:
            cache_paths = sorted(self.cache_dir.glob("*.json"), key=lambda path: int(path.stem))
        except ValueError as error:
            raise StateError("cache contains a non-numeric page-state filename") from error

        paths: dict[str, Path] = {}
        for cache_path in cache_paths:
            if not cache_path.is_file():
                raise StateError(f"cache entry '{cache_path.name}' is not a file")
            paths[cache_path.stem] = cache_path

        return paths

    def page_tree(self) -> PageTree:
        """Load every cached page state as a tree anchored at this workarea's root page."""
        states = {page_id: PageState.load(path) for page_id, path in self.page_state_paths().items()}
        return PageTree(states, self.root_page_id)

    def page_directory(self, state: PageState, must_exist: bool = True) -> Path:
        """Return the safe managed path, normally requiring a directory and page.md."""
        directory = self.page_directory_path(state.page.directory)
        if not must_exist:
            return directory

        if not directory.is_dir():
            raise Workarea.Error("managed page directory does not exist")
        if not (directory / "page.md").is_file():
            raise Workarea.Error("managed page directory does not contain page.md")

        return directory

    def remove_page(self, state: PageState) -> None:
        """Remove one complete managed page directory."""
        directory = self.page_directory(state)
        if _is_windows() and _current_directory_is_inside(directory):
            raise Workarea.Error("cannot remove a page directory while it is the current directory; run cflsync from outside it")

        try:
            shutil.rmtree(directory)
        except OSError as error:
            raise Workarea.Error(f"cannot remove managed page directory: {filesystem_error_message(error)}") from error

    def page_directory_target(self, state: PageState) -> Path:
        """Return a safe, unoccupied target path for a page directory."""
        directory = self.page_directory_path(state.page.directory)
        for other_id, path in self.page_state_paths().items():
            other = PageState.load(path)
            if other_id != state.page.id and other.page.directory.casefold() == state.page.directory.casefold():
                raise Workarea.Error(f"page directory '{state.page.directory}' is assigned to page '{other_id}'")

        name = state.page.directory.split("/")[-1]
        if directory.parent.is_dir():
            for existing in directory.parent.iterdir():
                if existing.name.casefold() == name.casefold() and existing != directory:
                    raise Workarea.Error(f"page directory '{state.page.directory}' already exists")

        cached_path = self.page_state_paths().get(state.page.id)
        cached_state = PageState.load(cached_path) if cached_path is not None else None
        if cached_state is not None and cached_state.page.directory != state.page.directory:
            source = self.page_directory_path(cached_state.page.directory)
            if _is_windows() and _current_directory_is_inside(source):
                raise Workarea.Error(
                    "cannot rename a page directory while it is the current directory; run cflsync from outside it")

        if directory.exists() and (cached_state is None or cached_state.page.directory != state.page.directory):
            raise Workarea.Error(f"page directory '{state.page.directory}' already exists")

        return directory

    def relocate(self, source: Path, directory: str) -> Path:
        """Move a page directory, with everything below it, to *directory* relative to the workarea root.

        The move is one directory rename. It is refused if another cached page is assigned the target directory, or
        if the target's parent already contains an entry with the same name, compared case-insensitively.
        """
        target = self.page_directory_path(directory)
        if target == source:
            return target

        for path in self.page_state_paths().values():
            other = PageState.load(path)
            if other.page.directory.casefold() == directory.casefold() and self.page_directory_path(other.page.directory) != source:
                raise Workarea.Error(f"page directory '{directory}' is assigned to page '{other.page.id}'")

        if target.parent.is_dir():
            for existing in target.parent.iterdir():
                if existing.name.casefold() == target.name.casefold() and existing != source:
                    raise Workarea.Error(f"page directory '{directory}' already exists")

        if _is_windows() and _current_directory_is_inside(source):
            raise Workarea.Error("cannot rename a page directory while it is the current directory; run cflsync from outside it")

        try:
            os.rename(source, target)
        except OSError as error:
            raise Workarea.Error(f"cannot move page directory: {filesystem_error_message(error)}") from error

        return target

    def page_directory_name(self, title: str) -> str:
        """Return the deterministic safe directory name for a page title."""
        if not isinstance(title, str) or not title:
            raise Workarea.Error("page title must be a non-empty string")

        directory_name = quote(title, safe=" -_").replace(".", "%2E")
        trailing_spaces = len(directory_name) - len(directory_name.rstrip(" "))
        directory_name = directory_name.rstrip(" ")
        if directory_name.upper() in _WINDOWS_RESERVED_NAMES:
            directory_name = f"%{ord(directory_name[0]):02X}{directory_name[1:]}"

        return f"{directory_name}{'%20' * trailing_spaces}"

    def stage_page(
        self,
        directory: str,
        markdown: str,
        attachments: Mapping[str, bytes],
        source: Path | None = None,
        managed_attachments: Iterable[str] = ()) -> Path:
        """Write one complete page representation to a hidden staging directory.

        *directory* is the page directory relative to the workarea root, with "/" separators.
        """
        self.page_directory_path(directory)
        if not isinstance(markdown, str):
            raise Workarea.Error("page Markdown must be a string")

        staging = Path(mkdtemp(prefix=".cflsync-stage-", dir=self.root_dir))
        try:
            if source is not None:
                shutil.copytree(source, staging, dirs_exist_ok=True, symlinks=True)

            page_path = staging / "page.md"
            page_path.unlink(missing_ok=True)
            attachment_directory = staging / "_attachments"
            if attachment_directory.is_symlink():
                raise Workarea.Error("attachment directory must not be a symbolic link")

            attachment_directory.mkdir(exist_ok=True)
            for filename in managed_attachments:
                self._attachment_path(attachment_directory, filename).unlink(missing_ok=True)

            for filename in attachments:
                path = self._attachment_path(attachment_directory, filename)
                if path.exists() or path.is_symlink():
                    raise Workarea.Error(f"attachment '{filename}' would overwrite an unmanaged file")

            (staging / "page.md").write_text(markdown, encoding="utf-8", newline="\n")
            for filename, body in attachments.items():
                self._attachment_path(attachment_directory, filename).write_bytes(body)

            return staging
        except SyncError:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        except (OSError, TypeError) as error:
            shutil.rmtree(staging, ignore_errors=True)
            raise Workarea.Error(f"cannot stage page directory: {filesystem_error_message(error)}") from error
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    @contextmanager
    def replace_page(self,
                     staging: Path,
                     directory: str,
                     source: Path | None = None,
                     managed_attachments: Iterable[str] = ()) -> Iterator[Path]:
        """Replace managed files, retaining backups until the caller commits state.

        *directory* is the target page directory relative to the workarea root, with "/" separators.
        """
        target = self.page_directory_path(directory)
        if source is not None:
            try:
                source_directory = source.relative_to(self.root_dir).as_posix()
            except ValueError as error:
                raise Workarea.Error("previous page directory must be inside the workarea") from error

            if source.is_symlink() or source != self.page_directory_path(source_directory):
                raise Workarea.Error("previous page directory must be a page directory inside the workarea")

        if target.exists() and target != source:
            raise Workarea.Error(f"page directory '{directory}' already exists")

        if source is None:
            self.install_page(staging, directory)
            try:
                yield target
            except BaseException:
                os.replace(target, staging)
                raise

            return

        paths = [Path("page.md")]
        for filename in sorted(set(managed_attachments)):
            self._attachment_path(source / "_attachments", filename)
            paths.append(Path("_attachments") / filename)

        backup = Path(mkdtemp(prefix=".cflsync-backup-", dir=self.root_dir))
        (backup / "_attachments").mkdir()
        changed = []
        renamed = False
        created_attachments = False
        cleanup = False
        try:
            if source != target:
                self.relocate(source, directory)
                renamed = True

            attachment_directory = target / "_attachments"
            if attachment_directory.is_symlink():
                raise Workarea.Error("attachment directory must not be a symbolic link")

            if not attachment_directory.exists():
                attachment_directory.mkdir()
                created_attachments = True

            for relative in paths:
                destination = target / relative
                replacement = staging / relative
                existed = destination.exists() or destination.is_symlink()
                if existed:
                    shutil.copy2(destination, backup / relative, follow_symlinks=False)

                if replacement.exists() or replacement.is_symlink():
                    os.replace(replacement, destination)
                elif existed:
                    destination.unlink()

                changed.append((relative, existed))

            yield target
            cleanup = True
        except BaseException:
            for relative, existed in reversed(changed):
                if existed:
                    os.replace(backup / relative, target / relative)
                else:
                    (target / relative).unlink(missing_ok=True)

            if created_attachments:
                (target / "_attachments").rmdir()

            if renamed:
                os.rename(target, source)

            cleanup = True

            raise
        finally:
            if cleanup:
                shutil.rmtree(backup, ignore_errors=True)

    def install_page(self, staging: Path, directory: str, replace: bool = False) -> Path:
        """Install a new page directory or atomically replace its files.

        *directory* is the page directory relative to the workarea root, with "/" separators.
        """
        target = self.page_directory_path(directory)
        if target.exists() and not replace:
            raise Workarea.Error(f"page directory '{directory}' already exists")

        try:
            staging = staging.resolve()
            staging.relative_to(self.root_dir)
        except ValueError as error:
            raise Workarea.Error("staging directory is outside the workarea") from error

        if not staging.is_dir() or not (staging / "page.md").is_file() or not (staging / "_attachments").is_dir():
            raise Workarea.Error("staging directory is incomplete")

        if not target.exists():
            try:
                os.replace(staging, target)
            except OSError as error:
                raise Workarea.Error(f"cannot install page directory: {filesystem_error_message(error)}") from error

            return target

        filenames = {path.name for path in (target / "_attachments").iterdir()}
        filenames.update(path.name for path in (staging / "_attachments").iterdir())
        try:
            with self.replace_page(staging, directory, target, filenames):
                pass
        except OSError as error:
            raise Workarea.Error(f"cannot replace page directory: {filesystem_error_message(error)}") from error

        shutil.rmtree(staging, ignore_errors=True)

        return target

    def page_directory_path(self, directory: str) -> Path:
        """Return the resolved path of a page directory given relative to the workarea root.

        *directory* separates directory names with "/" on every platform, as :meth:`PageTree.directory` returns them.
        The resolved path must stay inside the workarea.
        """
        components = directory.split("/")
        for component in components:
            if not component or component in {".", ".."} or Path(component).name != component:
                raise Workarea.Error(f"page directory '{directory}' must be a relative path of directory names")

        path = self.root_dir.joinpath(*components)
        path = path.resolve()
        try:
            path.relative_to(self.root_dir)
        except ValueError as error:
            raise Workarea.Error("page directory is outside the workarea") from error
        if path == self.root_dir:
            raise Workarea.Error("page directory must be below the workarea root")

        return path

    def _attachment_path(self, attachment_directory: Path, filename: str) -> Path:
        if not isinstance(filename, str) or not filename or filename in {".", ".."}:
            raise Workarea.Error("attachment filename must be a non-empty basename")

        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise Workarea.Error(f"attachment filename '{filename}' is unsafe")

        path = attachment_directory / filename

        return path

    @classmethod
    def find(cls, p: Path):
        """Locate the workarea that contains path p, refusing a workarea that is not anchored at a root page."""
        dir = cls._locate(p)
        if dir is None:
            raise Workarea.Error(f"'{p}' is not part of a cflsync workarea")

        workarea = cls(dir)
        # Reading the root page ID validates the workarea format.
        workarea.root_page_id
        return workarea

    @classmethod
    def _locate(cls, p):
        dir = (p if p.is_dir() else p.parent).resolve()
        while True:
            cflsync_dir = dir / ".cflsync"
            if cflsync_dir.is_dir() and (cflsync_dir / "profile").is_file():
                return dir

            if dir == dir.parent:
                return None

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
            return cls.resolve_remote(text, api)

        paths = workarea.page_state_paths()
        states = {page_id: PageState.load(path) for page_id, path in paths.items()}
        cached_ids = [state.page.id for state in states.values() if state.page.title == text]
        if cached_ids:
            return cls(_one_page_ref_id(cached_ids, f"cached title '{text}'"))

        return cls.resolve_remote(text, api)

    @classmethod
    def resolve_remote(cls, value: str, api) -> "PageRef":
        """Resolve a page ID or title to one Confluence page ID through the API, without a workarea."""
        if value.isdigit():
            return cls(api.get_page(value).id)

        pages = [page for page in api.find_pages_by_title(value) if page.title == value]
        return cls(_one_page_ref_id([page.id for page in pages], f"title '{value}'"))

    @classmethod
    def resolve_local(cls, value: str | Path, workarea: Workarea, cwd: Path | None = None) -> "PageRef":
        """Resolve a local managed page path, ID, or title without remote access."""
        text = str(value)
        path = _page_ref_path(value, cwd)
        if path.exists():
            return cls._from_path(path, workarea)

        paths = workarea.page_state_paths()
        states = {page_id: PageState.load(path) for page_id, path in paths.items()}
        if text.isdigit() and text in states:
            return cls(text)

        cached_ids = [state.page.id for state in states.values() if state.page.title == text]
        if cached_ids:
            return cls(_one_page_ref_id(cached_ids, f"cached title '{text}'"))

        raise PageRefError(f"no managed local page matches '{text}'")

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
        paths = workarea.page_state_paths()
        states = {page_id: PageState.load(path) for page_id, path in paths.items()}
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


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3",
    "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9", }


def _is_windows() -> bool:
    return os.name == "nt"


def _current_directory_is_inside(directory: Path) -> bool:
    try:
        Path.cwd().resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False

    return True


def filesystem_error_message(error: Exception) -> str:
    """Describe a filesystem error, explaining a path that the operating system rejected as too long."""
    if not isinstance(error, OSError) or not _is_path_length_error(error):
        return str(error)

    paths = [str(path) for path in (error.filename, error.filename2) if path is not None]
    if not paths:
        return f"a path is too long for this system: {error}"

    path = max(paths, key=len)
    return f"path is too long for this system ({len(path)} characters): '{path}'; on Windows, enable long path support"


def _is_path_length_error(error):
    # Windows reports ERROR_FILENAME_EXCED_RANGE (206); other systems report ENAMETOOLONG.
    return error.errno == errno.ENAMETOOLONG or getattr(error, "winerror", None) == 206


# vim: set ts=4 sw=4 et tw=132:
