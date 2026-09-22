# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Confluence request transports."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from base64 import b64encode
from collections.abc import Mapping
from typing import Protocol

from .errors import SyncError


class TransportError(SyncError):
    """Raised when a transport cannot send a request."""


class TransportResponse:
    """An in-memory response returned by a :class:`Transport`."""

    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = dict(headers)
        self.body = body


class Transport(Protocol):
    """A Confluence request context with host credentials and a URL prefix."""

    def clone(self, prefix: str | None = None) -> "Transport":
        """Return an equivalent transport using *prefix*."""
        ...

    def make_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> TransportResponse:
        """Send one request relative to the configured prefix."""
        ...


class UrllibTransport:
    """A :class:`Transport` implementation backed by :mod:`urllib.request`."""

    def __init__(self, host: str, username: str, password: str, prefix: str) -> None:
        self._host = host
        self._username = username
        self._password = password
        self._prefix = prefix
        auth = urllib.request.HTTPPasswordMgrWithPriorAuth()
        auth.add_password(realm=None, uri=self.host_url(), user=username, passwd=password, is_authenticated=True)
        auth_handler = urllib.request.HTTPBasicAuthHandler(auth)
        self._opener = urllib.request.build_opener(auth_handler)

    def clone(self, prefix: str | None = None) -> "UrllibTransport":
        if prefix is None:
            prefix = self._prefix

        clone = object.__new__(UrllibTransport)
        clone._host = self._host
        clone._username = self._username
        clone._password = self._password
        clone._prefix = prefix
        clone._opener = self._opener
        return clone

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
        request = urllib.request.Request(
            self._request_url(path, parameters), data=body, headers=self._request_headers(headers), method=method)
        try:
            with self._opener.open(request) as response:
                result = TransportResponse(status=response.status, headers=dict(response.headers.items()), body=response.read())
        except urllib.error.HTTPError as error:
            result = TransportResponse(
                status=error.code, headers=dict(error.headers.items()) if error.headers else {}, body=error.read())
        except urllib.error.URLError as error:
            raise TransportError(f"cannot reach Confluence API: {error.reason}") from error

        return result


# vim: set ts=4 sw=4 et tw=132:
