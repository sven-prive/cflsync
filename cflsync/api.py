# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Confluence API client operations."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .errors import SyncError
from .transport import Transport, TransportResponse, UrllibTransport


class APIError(SyncError):
    """Raised when a Confluence API response cannot satisfy an operation."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @classmethod
    def from_response(cls, response: "APIResponse") -> "APIError":
        message = f"Confluence API request failed with HTTP {response.status}"
        try:
            value = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass
        else:
            if isinstance(value, Mapping) and isinstance(value.get("message"), str):
                message = value["message"]

        return cls(message, response.status)


class APIResponse:
    """An API-level response returned by :class:`APIClient`."""

    def __init__(self, status: int, headers: Mapping[str, str], body: bytes) -> None:
        self.status = status
        self.headers = dict(headers)
        self.body = body

    @classmethod
    def from_transport(cls, response: TransportResponse) -> "APIResponse":
        return cls(response.status, response.headers, response.body)


class APIClient:
    """Confluence Cloud API operations over a :class:`Transport`."""

    def __init__(
            self,
            host: str,
            username: str,
            password: str,
            base_path: str = "/wiki/api/v2",
            transport: Transport | None = None) -> None:
        self._transport = transport or UrllibTransport(host, username, password, base_path)

    def make_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            body: bytes | None = None) -> APIResponse:
        """Send one request through the configured transport context."""
        return self._api_response(self._transport.make_request(method, path, parameters, headers, body))

    def make_json_request(
            self,
            method: str,
            path: str = "",
            parameters: Mapping[str, str] | None = None,
            headers: Mapping[str, str] | None = None,
            json_body: object | None = None) -> APIResponse:
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
                raise APIError("paginated response has no results list")
            results.extend(page_results)

            next_path = self._next_page_path(result)
            if next_path is None:
                return results

            next_path_transport = self._transport.clone("")
            response = self._api_response(next_path_transport.make_request("GET", next_path, headers=headers))

    @staticmethod
    def _api_response(response: TransportResponse) -> APIResponse:
        api_response = APIResponse.from_transport(response)
        if not 200 <= api_response.status < 300:
            raise APIError.from_response(api_response)

        return api_response

    @staticmethod
    def _json_object(response: APIResponse) -> Mapping[str, object]:
        try:
            value = json.loads(response.body)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise APIError("API response is not valid JSON") from error
        if not isinstance(value, Mapping):
            raise APIError("API response must be a JSON object")

        return value

    @staticmethod
    def _next_page_path(result: Mapping[str, object]) -> str | None:
        links = result.get("_links", {})
        if not isinstance(links, Mapping):
            raise APIError("pagination links must be a JSON object")
        next_path = links.get("next")
        if next_path is not None and not isinstance(next_path, str):
            raise APIError("pagination next link must be a string")

        return next_path


# vim: set ts=4 sw=4 et tw=132:
