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
import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from argparse import ArgumentParser
from collections.abc import Mapping
from getpass import getpass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol

from platformdirs import user_config_dir


class SyncError(Exception):

    def __init__(self, message):
        super().__init__(message)


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


class Config:

    class Profile:

        def __init__(self, hostname: str, username: str, apitoken: str):
            self.hostname = hostname
            self.username = username
            self.apitoken = apitoken

        @classmethod
        def from_json(cls, values: dict[str, str]) -> Config.Profile:
            return cls(hostname=values["hostname"], username=values["username"], apitoken=values["apitoken"])

        def to_json(self) -> dict[str, str]:
            return {"hostname": self.hostname, "username": self.username, "apitoken": self.apitoken}

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
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with open(self.path, "w") as f:
            json.dump(self.to_json(), f, indent=2)
            f.write("\n")
        self.path.chmod(0o600)

    def to_json(self) -> dict[str, dict[str, str]]:
        items = {}
        for name, profile in self.profiles.items():
            items[name] = profile.to_json()

        return items


class APIError(SyncError):

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @classmethod
    def from_http_error(cls, response: "TransportResponse") -> "APIError":
        error_type = {
            401: AuthenticationError,
            403: AuthorizationError,
            404: NotFoundError,
            409: ConflictError,
            412: ConflictError,
            429: RateLimitError}.get(response.status, cls)
        message = f"Confluence API request failed with HTTP {response.status}"
        try:
            value = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        else:
            if isinstance(value, Mapping) and isinstance(value.get("message"), str):
                message = value["message"]

        return error_type(message, response.status)


class AuthenticationError(APIError):
    """Raised for HTTP 401 responses."""


class AuthorizationError(APIError):
    """Raised for HTTP 403 responses."""


class NotFoundError(APIError):
    """Raised for HTTP 404 responses."""


class RateLimitError(APIError):
    """Raised for HTTP 429 responses."""


class ConflictError(APIError):
    """Raised for HTTP 409 and 412 version-conflict responses."""


class ResponseError(APIError):
    """Raised for malformed API response data."""


class TransportError(APIError):
    """Raised when an HTTP request cannot be sent."""


class TransportResponse(Protocol):
    """The response interface returned by an API transport."""

    status: int
    headers: Mapping[str, str]
    body: bytes


class HTTPTransport(Protocol):
    """A transport that sends one complete HTTP request."""

    def request(
            self,
            method: str,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> TransportResponse:
        ...


class UrllibHTTPTransport:
    """HTTP transport backed by :mod:`urllib.request`."""

    def __init__(self, host: str, username: str, password: str) -> None:
        auth = urllib.request.HTTPPasswordMgrWithPriorAuth()
        auth.add_password(realm=None, uri=f"https://{host}", user=username, passwd=password, is_authenticated=True)
        auth_handler = urllib.request.HTTPBasicAuthHandler(auth)
        self._opener = urllib.request.build_opener(auth_handler)

    def request(
            self,
            method: str,
            url: str,
            *,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> TransportResponse:
        request = urllib.request.Request(url, data=body, headers=dict(headers or {}), method=method)
        try:
            with self._opener.open(request) as response:
                return HTTPResponse(status=response.status, headers=dict(response.headers.items()), body=response.read())
        except urllib.error.HTTPError as error:
            return HTTPResponse(status=error.code, headers=dict(error.headers.items()) if error.headers else {}, body=error.read())
        except urllib.error.URLError as error:
            raise TransportError(f"cannot reach Confluence API: {error.reason}") from error


class HTTPResponse:
    """An in-memory HTTP response returned by :class:`UrllibTransport`."""

    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = dict(headers)
        self.body = body


class Transport:
    """An authenticated HTTP context with a URL prefix."""

    def __init__(self, host: str, username: str, password: str, prefix: str, http_transport: HTTPTransport) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._prefix = prefix
        self._http_transport = http_transport

    def clone(self, prefix: str | None = None) -> "Transport":
        if prefix is None:
            prefix = self._prefix

        return Transport(self._host, self._username, self._password, prefix, self._http_transport)

    def host_url(self) -> str:
        return f"https://{self._host}"

    def base_url(self) -> str:
        return f"{self.host_url()}{self._prefix}"

    def _request_url(self, path: str, parameters: Mapping[str, str] | None) -> str:
        parsed_path = urllib.parse.urlsplit(path)
        if parsed_path.scheme or parsed_path.netloc or parsed_path.fragment:
            raise TransportError("request path must be relative to the configured host")
        encoded_path = "/".join(urllib.parse.quote(part, safe="") for part in parsed_path.path.lstrip("/").split("/") if part)
        url = self.base_url()
        if encoded_path:
            url = f"{url}/{encoded_path}"
        query = parsed_path.query
        if parameters:
            encoded_parameters = urllib.parse.urlencode(parameters)
            query = f"{query}&{encoded_parameters}" if query else encoded_parameters
        if query:
            url = f"{url}?{query}"

        return url

    def _request_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        encoded_credentials = b64encode(f"{self._username}:{self._password}".encode("utf-8")).decode("ascii")
        request_headers = {"Accept": "application/json", "Authorization": f"Basic {encoded_credentials}"}
        request_headers.update(headers or {})

        return request_headers

    def make_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> TransportResponse:
        """Send one request relative to this context's prefix."""
        response = self._http_transport.request(
            method, self._request_url(path, parameters), headers=self._request_headers(headers), body=body)
        if not 200 <= response.status < 300:
            raise APIError.from_http_error(response)

        return response


class APIClient:

    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            base_path: str = "/wiki/api/v2",
            transport: HTTPTransport | None = None) -> None:
        self._transport = Transport(host, username, password, base_path, transport or UrllibHTTPTransport(host, username, password))

    def make_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> TransportResponse:
        """Send one request through the configured transport context."""
        return self._transport.make_request(method, path, parameters, headers, body)

    def make_json_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            json_body: object | None = None) -> TransportResponse:
        """Send a request with an optional JSON-encoded body."""
        request_headers = dict(headers or {})
        body = None
        if json_body is not None:
            request_headers.setdefault("Content-Type", "application/json")
            body = json.dumps(json_body).encode("utf-8")

        return self.make_request(method, path, parameters, request_headers, body)

    def make_paginated_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> list[object]:
        """Send a paginated request and return its combined results."""
        response = self.make_request(method, path, parameters, headers, body)
        results: list[object] = []

        while True:
            result = self._json_object(response)
            page_results = result.get("results")
            if not isinstance(page_results, list):
                raise ResponseError("paginated response has no results list")
            results.extend(page_results)

            next_path = self._next_page_path(result)
            if next_path is None:
                return results

            next_path_transport = self._transport.clone("")
            response = next_path_transport.make_request("GET", next_path, headers=headers)

    @staticmethod
    def _json_object(response: TransportResponse) -> Mapping[str, object]:
        try:
            value = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ResponseError("API response is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise ResponseError("API response must be a JSON object")

        return value

    @staticmethod
    def _next_page_path(result: Mapping[str, object]) -> str | None:
        links = result.get("_links", {})
        if not isinstance(links, Mapping):
            raise ResponseError("pagination links must be a JSON object")
        next_path = links.get("next")
        if next_path is not None and not isinstance(next_path, str):
            raise ResponseError("pagination next link must be a string")

        return next_path


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


class InitCommand:

    def configure(self, sp):
        init_parser = sp.add_parser("init", help="initialise a cflsync workarea in the current directory")
        init_parser.add_argument("-p", "--profile", default="default", help="use PROFILE instead of 'default'")
        init_parser.set_defaults(command=self)

    def __call__(self, args):
        wa = Workarea.init(Path.cwd(), args.profile)

        return 0


class AuthCommand:

    def configure(self, sp):
        auth_parser = sp.add_parser("auth", help="store Confluence Cloud credentials")
        auth_parser.add_argument("-p", "--profile", default="default", help="store credentials under PROFILE instead of 'default'")
        auth_action = auth_parser.add_mutually_exclusive_group()
        auth_action.add_argument("-l", "--list", action="store_true", help="list available profiles")
        auth_action.add_argument("-d", "--delete", action="store_true", help="delete stored credentials")
        auth_parser.set_defaults(command=self)

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
        page_pull_parser = sp.add_parser("pull", help="pull a page from Confluence Cloud")
        page_pull_parser.add_argument("page_ref", help="page id or title of the page to pull")
        page_pull_parser.set_defaults(command=self)

    def __call__(self, args):
        pass


class PageCommand:

    def configure(self, sp):
        # Dirty trick: cache the page command parser so we can print help later
        self.page_parser = sp.add_parser("page", help="page commands")
        self.page_parser.set_defaults(command=self)
        ssp = self.page_parser.add_subparsers(title="page commands", metavar="command")

        PagePullCommand().configure(ssp)

    def __call__(self, args):
        # The page command is not meant to be invoked on its own
        self.page_parser.print_usage()


def main(argv):
    ap = ArgumentParser(prog=argv[0])
    # Avoid "error: " message from the argument parser by making the subparser
    # commands required
    ap.set_defaults(command=lambda args: ap.print_usage())
    sp = ap.add_subparsers(title="commands", metavar="command")

    AuthCommand().configure(sp)
    InitCommand().configure(sp)
    PageCommand().configure(sp)

    args = ap.parse_args(argv[1:])

    return args.command(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

# vim: set ts=4 sw=4 et tw=132:
