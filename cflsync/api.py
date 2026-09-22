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


class RemotePage:
    """The page fields used by cflsync synchronization operations."""

    def __init__(
            self, client: "APIClient", id: str, title: str, version: int, body: str | None, space_id: str | None,
            parent_id: str | None) -> None:
        self._client = client
        self.id = id
        self.title = title
        self.version = version
        self.body = body
        self.space_id = space_id
        self.parent_id = parent_id

    @classmethod
    def from_json(cls, client: "APIClient", value: Mapping[str, object]) -> "RemotePage":
        id = _required_string(value, "page", "id")
        title = _required_string(value, "page", "title")
        version_value = _required_object(value, "page", "version")
        version = _required_int(version_value, "page.version", "number")
        body = _optional_body(value)
        space_id = _optional_string(value, "spaceId")
        parent_id = _optional_string(value, "parentId")

        return cls(client, id, title, version, body, space_id, parent_id)

    def update(self, body: str) -> "RemotePage":
        """Update this page using its current version for optimistic concurrency."""
        response = self._client.make_json_request(
            "PUT",
            f"/pages/{self.id}",
            json_body={
                "id": self.id,
                "status": "current",
                "title": self.title,
                "body": {
                    "representation": "atlas_doc_format",
                    "value": body},
                "version": {
                    "number": self.version + 1}})
        return RemotePage.from_json(self._client, self._client._json_object(response))

    def attachments(self) -> list["RemoteAttachment"]:
        """Return every attachment belonging to this page."""
        values = self._client.make_paginated_request("GET", f"/pages/{self.id}/attachments")
        return [RemoteAttachment.from_json(self._client, _json_mapping(value, "attachment result"), self.id) for value in values]

    def create_attachment(self, filename: str, body: bytes) -> "RemoteAttachment":
        """Create an attachment on this page."""
        response = self._client._v1_multipart_request("PUT", f"/content/{self.id}/child/attachment", filename, body)
        result = self._client._json_object(response).get("results")
        if not isinstance(result, list) or len(result) != 1:
            raise APIError("attachment upload response must contain one result")

        return RemoteAttachment.from_json(self._client, _json_mapping(result[0], "attachment upload result"), self.id)


class RemoteAttachment:
    """The attachment fields used by cflsync synchronization operations."""

    def __init__(
            self, client: "APIClient", id: str, filename: str, version: int, media_type: str | None, download_path: str | None,
            page_id: str | None) -> None:
        self._client = client
        self.id = id
        self.filename = filename
        self.version = version
        self.media_type = media_type
        self.download_path = download_path
        self.page_id = page_id

    @classmethod
    def from_json(cls, client: "APIClient", value: Mapping[str, object], page_id: str | None = None) -> "RemoteAttachment":
        id = _required_string(value, "attachment", "id")
        filename = _required_string(value, "attachment", "title")
        version_value = _required_object(value, "attachment", "version")
        version = _required_int(version_value, "attachment.version", "number")
        media_type = _optional_string(value, "mediaType")
        download_path = _attachment_download_path(value)
        page_id = page_id or _optional_string(value, "pageId")

        return cls(client, id, filename, version, media_type, download_path, page_id)

    def download(self) -> bytes:
        """Download this attachment's bytes through its server-provided link."""
        if self.download_path is None:
            raise APIError(f"attachment '{self.id}' has no download link")

        transport = self._client._transport.clone("")
        response = self._client._api_response(transport.make_request("GET", self.download_path))
        return response.body

    def update(self, body: bytes) -> "RemoteAttachment":
        """Replace this attachment's bytes using its stable remote ID."""
        if self.page_id is None:
            raise APIError(f"attachment '{self.id}' has no page ID")

        response = self._client._v1_multipart_request(
            "POST", f"/content/{self.page_id}/child/attachment/{self.id}/data", self.filename, body)
        return RemoteAttachment.from_json(self._client, self._client._json_object(response), self.page_id)

    def delete(self) -> None:
        """Delete this attachment."""
        self._client.make_request("DELETE", f"/attachments/{self.id}")


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

    def get_page(self, page_id: str) -> RemotePage:
        """Return a page with its Atlas Document Format body."""
        response = self.make_request(
            "GET", f"/pages/{page_id}", parameters={
                "body-format": "atlas_doc_format",
                "include-version": "true"})
        return RemotePage.from_json(self, self._json_object(response))

    def find_pages_by_title(self, title: str) -> list[RemotePage]:
        """Return pages whose remote title matches *title*."""
        values = self.make_paginated_request(
            "GET", "/pages", parameters={
                "title": title,
                "body-format": "atlas_doc_format",
                "include-version": "true"})
        pages = [RemotePage.from_json(self, _json_mapping(value, "page result")) for value in values]
        return [page for page in pages if page.title == title]

    def create_page(self, space_id: str, parent_id: str, title: str) -> RemotePage:
        """Create an empty child page in *space_id*."""
        response = self.make_json_request(
            "POST",
            "/pages",
            json_body={
                "spaceId": space_id,
                "status": "current",
                "title": title,
                "parentId": parent_id,
                "body": {
                    "representation": "atlas_doc_format",
                    "value": _empty_adf()}})
        return RemotePage.from_json(self, self._json_object(response))

    def _v1_multipart_request(self, method: str, path: str, filename: str, body: bytes) -> APIResponse:
        boundary, multipart_body = _multipart_body(filename, body)
        transport = self._transport.clone("/wiki/rest/api")
        response = transport.make_request(
            method,
            path,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Atlassian-Token": "nocheck"},
            body=multipart_body)
        return self._api_response(response)

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


def _required_string(value: Mapping[str, object], object_name: str, field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise APIError(f"{object_name}.{field} must be a non-empty string")

    return result


def _required_int(value: Mapping[str, object], object_name: str, field: str) -> int:
    result = value.get(field)
    if type(result) is not int or result < 1:
        raise APIError(f"{object_name}.{field} must be a positive integer")

    return result


def _required_object(value: Mapping[str, object], object_name: str, field: str) -> Mapping[str, object]:
    result = value.get(field)
    if not isinstance(result, Mapping):
        raise APIError(f"{object_name}.{field} must be an object")

    return result


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    result = value.get(field)
    if result is None:
        return None
    if not isinstance(result, str):
        raise APIError(f"{field} must be a string")

    return result


def _optional_body(value: Mapping[str, object]) -> str | None:
    body = value.get("body")
    if body is None:
        return None
    if not isinstance(body, Mapping):
        raise APIError("page.body must be an object")
    adf = body.get("atlas_doc_format")
    if adf is None:
        return None
    if not isinstance(adf, Mapping):
        raise APIError("page.body.atlas_doc_format must be an object")
    return _required_string(adf, "page.body.atlas_doc_format", "value")


def _attachment_download_path(value: Mapping[str, object]) -> str | None:
    links = value.get("_links")
    if links is None:
        return _optional_string(value, "downloadLink")
    if not isinstance(links, Mapping):
        raise APIError("attachment._links must be an object")

    return _optional_string(links, "download")


def _json_mapping(value: object, object_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise APIError(f"{object_name} must be a JSON object")

    return value


def _empty_adf() -> str:
    return json.dumps({"type": "doc", "version": 1, "content": []}, separators=(",", ":"))


def _multipart_body(filename: str, body: bytes) -> tuple[str, bytes]:
    if not filename or "\r" in filename or "\n" in filename:
        raise APIError("attachment filename must be a non-empty single-line string")

    boundary = "cflsync-boundary"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n").encode("utf-8")
    suffix = (f"\r\n--{boundary}\r\n"
              'Content-Disposition: form-data; name="minorEdit"\r\n\r\n'
              f"false\r\n--{boundary}--\r\n").encode("utf-8")

    return boundary, prefix + body + suffix


# vim: set ts=4 sw=4 et tw=132:
