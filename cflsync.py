#! /usr/bin/env python3
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

# Delay annotation evaluation so nested type references work on Python 3.11+.
from __future__ import annotations

import json
import os
import re
import sys
import urllib
from argparse import ArgumentParser
from collections.abc import Mapping
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile

from platformdirs import user_config_dir


class SyncError(Exception):
    def __init__(self, message):
        super().__init__(message)


class StateError(SyncError):
    """Raised when a page synchronization state file is invalid."""


class PageMetadata:
    """The last synchronized state of one Confluence page."""

    def __init__(
        self, id: str, title: str, directory: str, version: int, content_hash: str
    ) -> None:
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

        return (
            self.id,
            self.title,
            self.directory,
            self.version,
            self.content_hash,
        ) == (
            other.id,
            other.title,
            other.directory,
            other.version,
            other.content_hash,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "title": self.title,
            "directory": self.directory,
            "version": self.version,
            "content_hash": self.content_hash,
        }

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
            raise StateError(
                "attachment.content_hash must be a SHA-256 hexadecimal digest"
            )

        self.id = id
        self.version = version
        self.content_hash = content_hash

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, AttachmentMetadata):
            return NotImplemented

        return (self.id, self.version, self.content_hash) == (
            other.id,
            other.version,
            other.content_hash,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "id": self.id,
            "version": self.version,
            "content_hash": self.content_hash,
        }

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
            raise StateError(
                f"attachment '{name}'.{error.args[0]} is required"
            ) from error
        if not isinstance(id, str):
            raise StateError(f"attachment '{name}'.id must be a string")
        if type(version) is not int:
            raise StateError(f"attachment '{name}'.version must be an integer")
        if not isinstance(content_hash, str):
            raise StateError(f"attachment '{name}'.content_hash must be a string")

        return cls(id, version, content_hash)


class PageState:
    """Format-1 synchronization state for one managed page."""

    def __init__(
        self,
        page: PageMetadata,
        attachments: Mapping[str, AttachmentMetadata],
        format: int = 1,
    ) -> None:
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

        return (
            self.page,
            self.attachments,
            self.format,
        ) == (
            other.page,
            other.attachments,
            other.format,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "format": self.format,
            "page": self.page.to_json(),
            "attachments": {
                name: attachment.to_json()
                for name, attachment in self.attachments.items()
            },
        }

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

        return cls(
            page=PageMetadata.from_json(page_value),
            attachments=attachments,
            format=state_format,
        )

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
            raise StateError(
                f"state file '{path.name}' does not match page.id '{state.page.id}'"
            )

        return state

    def save(self, workarea: "Workarea") -> None:
        """Atomically persist this state under its page-ID cache key."""
        try:
            path = workarea.cache_path(self.page.id)
        except ValueError as error:
            raise StateError(str(error)) from error

        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{self.page.id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_path.chmod(0o600)
                json.dump(self.to_json(), temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, path)
        except OSError as error:
            raise StateError(
                f"cannot write state file for page '{self.page.id}': {error}"
            ) from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except FileNotFoundError:
                    pass
class Config:
    class Profile:
        def __init__(self, hostname: str, username: str, apitoken: str):
            self.hostname = hostname
            self.username = username
            self.apitoken = apitoken

        @classmethod
        def from_json(cls, values: dict[str, str]) -> Config.Profile:
            return cls(
                hostname = values["hostname"],
                username = values["username"],
                apitoken = values["apitoken"],
            )

        def to_json(self) -> dict[str, str]:
            return {
                "hostname": self.hostname,
                "username": self.username, 
                "apitoken": self.apitoken,
            }

    def __init__(self, path: Path, profiles: dict[str, Config.Profile] | None = None):
        self.path = path
        self._profiles = profiles or {}

    @classmethod
    def find(cls):
        path = Path(user_config_dir("cflsync")) / "config.json"
        if not path.exists():
            return cls(path)

        with open(path, "r") as f:
            items = json.load(f)

        profiles = {}
        for name, values in items.items():
            profiles[name] = Config.Profile.from_json(values)

        return cls(path, profiles)

    @property
    def profiles(self) -> dict[str, Config.Profile]:
        return self._profiles

    @profiles.setter
    def profiles(self, value):
        raise AttributeError("can't assign to read-only property 'profiles'")

    @profiles.deleter
    def profiles(self):
        raise AttributeError("can't delete read-only property 'profiles'")

    def save(self):
        self.path.parent.mkdir(mode = 0o700, parents = True, exist_ok = True)
        self.path.parent.chmod(0o700)
        with open(self.path, "w") as f:
            json.dump(self.to_json(), f, indent = 2)
            f.write("\n")
        self.path.chmod(0o600)

    def to_json(self) -> dict[str, dict[str, str]]:
        items = {}
        for name, profile in self.profiles.items():
            items[name] = profile.to_json()

        return items


class APIError(SyncError):
    @classmethod
    def from_http_error(cls):
        pass


class APIClient():
    _host: str
    _username: str
    _password: str
    _base_path: str
    _opener : urllib.request.OpenerDirector

    def __init__(self, host, username, password, base_path="/wiki/api/v2"):
        auth = urllib.request.HTTPPasswordMgrWithPriorAuth()
        auth.add_password(
            realm = None,
            uri = f"https://{host}",
            user = username,
            passwd = password,
            is_authenticated = True,
        )

        auth_handler = urllib.request.HTTPBasicAuthHandler(auth)

        self._host = host
        self._username = username
        self._password = password
        self._base_path = base_path
        self._opener = urllib.request.build_opener(auth_handler)

    def clone(self, base_path = None):
        if base_path is None:
            base_path = self._base_path

        return Context(self._host, self._username, self._password, base_path)

    def host_url(self) -> str:
        return f"https://{self._host}"

    def base_url(self) -> str:
        return f"{self.host_url()}{self._base_path}"

    def open_url(self, request: urllib.request.Request):
        return self._opener.open(request)

    def make_request(self, method, parameters = {}, headers = {}, body = None):
        url = self.url
        if len(parameters) > 0:
            url = f"{url}?{urllib.parse.urlencode(parameters)}"

        req = urllib.request.Request(url, method = method, headers = headers, data = body)

        return self.context.open_url(req)

    def make_paginated_request(self, method, parameters = {}, headers = {}, body = None):
        results = []
        response = self.make_request(method, parameters, headers, body)

        while True:
            if not self.is_ok(response):
                return response

            result = json.load(response)
            results.extend(result.get("results", []))

            next_path = result.get("_links", {}).get("next")
            if next_path is None:
                break

            # _links.next is relative to the host root, not the API root
            next_resource = Resource(self.context.clone(""), next_path)
            response = next_resource.make_request("GET", {}, headers)

        return results


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
        cflsync_dir.mkdir(mode = 0o700)
        cflsync_dir.chmod(0o700)
        cache_dir.mkdir(mode = 0o700)
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
            cache_paths = sorted(
                self.cache_dir.glob("*.json"), key=lambda path: int(path.stem)
            )
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
                    f"page directory '{state.page.directory}' is assigned to both "
                    f"'{assigned_page_id}' and '{state.page.id}'"
                )
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
        if directory.exists() and (
            cached_state is None or cached_state.page.directory != state.page.directory
        ):
            raise Workarea.Error(
                f"page directory '{state.page.directory}' already exists"
            )

        return directory

    def _page_directory_path(self, directory_name: str) -> Path:
        directory = Path(directory_name)
        if (
            directory.is_absolute()
            or directory.name != directory_name
            or directory_name in {".", ".."}
        ):
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


class InitCommand:
    def configure(self, sp):
        init_parser = sp.add_parser("init", help = "initialise a cflsync workarea in the current directory")
        init_parser.add_argument("-p", "--profile", default = "default", help = "use PROFILE instead of 'default'")
        init_parser.set_defaults(command = self)

    def __call__(self, args):
        wa = Workarea.init(Path.cwd(), args.profile)

        return 0


class AuthCommand:
    def configure(self, sp):
        auth_parser = sp.add_parser("auth", help = "store Confluence Cloud credentials")
        auth_parser.add_argument("-p", "--profile", default = "default", help = "store credentials under PROFILE instead of 'default'")
        auth_action = auth_parser.add_mutually_exclusive_group()
        auth_action.add_argument("-l", "--list", action = "store_true", help = "list available profiles")
        auth_action.add_argument("-d", "--delete", action = "store_true", help = "delete stored credentials")
        auth_parser.set_defaults(command = self)

    def __call__(self, args):
        config = Config.find()
        if args.list:
            for name in config.profiles:
                print(name)

            return 0

        if args.delete:
            if args.profile not in config.profiles:
                return 0

            answer = input(f"Delete profile '{args.profile}'? [y/N] ")
            if answer.lower() in ["y", "yes"]:
                del config.profiles[args.profile]
                config.save()

            return 0

        hostname = input("Confluence Cloud hostname: ")
        username = input("Confluence Cloud username: ")
        apitoken = getpass("Confluence Cloud API token: ")

        config.profiles[args.profile] = Config.Profile(hostname, username, apitoken)
        config.save()

        return 0


class PagePullCommand:
    def configure(self, sp):
        page_pull_parser = sp.add_parser("pull", help = "pull a page from Confluence Cloud")
        page_pull_parser.add_argument("page_ref", help = "page id or title of the page to pull")
        page_pull_parser.set_defaults(command = self)

    def __call__(self, args):
        pass


class PageCommand:
    def configure(self, sp):
        # Dirty trick: cache the page command parser so we can print help later
        self.page_parser = sp.add_parser("page", help = "page commands")
        self.page_parser.set_defaults(command = self)
        ssp = self.page_parser.add_subparsers(title = "page commands", metavar = "command")

        PagePullCommand().configure(ssp)

    def __call__(self, args):
        # The page command is not meant to be invoked on its own
        self.page_parser.print_usage()


def main(argv):
    ap = ArgumentParser(prog = argv[0])
    # Avoid "error: " message from the argument parser by making the subparser
    # commands required
    ap.set_defaults(command = lambda args: ap.print_usage())
    sp = ap.add_subparsers(title = "commands", metavar = "command")

    AuthCommand().configure(sp)
    InitCommand().configure(sp)
    PageCommand().configure(sp)

    args = ap.parse_args(argv[1:])

    return args.command(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
