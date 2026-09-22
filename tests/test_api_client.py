# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for API client transport behavior."""

from base64 import b64encode
import json
import unittest

from cflsync import (
    APIClient, AuthenticationError, AuthorizationError, ConflictError, NotFoundError, RateLimitError, ResponseError, Transport)
from tests.support import MockResponse, MockTransport


class TestAPIClientTransport(unittest.TestCase):

    def test_uses_the_injected_transport(self) -> None:
        response = MockResponse.from_json({"id": "123456"})
        transport = MockTransport([response])
        client = APIClient("example.atlassian.net", "user@example.test", "token", transport=transport)

        self.assertIs(client.make_request("GET"), response)
        self.assertEqual(len(transport.requests), 1)

    def test_transport_clone_reuses_http_transport_with_a_new_prefix(self) -> None:
        http_transport = MockTransport([MockResponse.from_json({})])
        transport = Transport("example.atlassian.net", "user", "token", "/wiki/api/v2", http_transport)

        transport.clone("/wiki/api/v3").make_request("GET")

        self.assertEqual(http_transport.requests[0].url, "https://example.atlassian.net/wiki/api/v3")

    def test_encodes_a_json_request_with_basic_authentication(self) -> None:
        transport = MockTransport([MockResponse.from_json({})])
        client = APIClient("example.atlassian.net", "user@example.test", "token", transport=transport)

        client.make_json_request(
            "PUT",
            "/pages/A page",
            parameters={
                "expand": "body.atlas_doc_format",
                "title": "A page"},
            json_body={"title": "A page"})

        request = transport.requests[0]
        credentials = b64encode(b"user@example.test:token").decode("ascii")
        self.assertEqual(request.method, "PUT")
        self.assertEqual(
            request.url, "https://example.atlassian.net/wiki/api/v2/pages/A%20page?expand=body.atlas_doc_format&title=A+page")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(request.headers["Authorization"], f"Basic {credentials}")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(request.body), {"title": "A page"})

    def test_collects_paginated_results_from_host_relative_links(self) -> None:
        transport = MockTransport(
            [
                MockResponse.from_json({
                    "results": [{
                        "id": "1"}],
                    "_links": {
                        "next": "/wiki/api/v2/pages?cursor=next"}}),
                MockResponse.from_json({"results": [{
                    "id": "2"}]})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        self.assertEqual(client.make_paginated_request("GET", "/pages"), [{"id": "1"}, {"id": "2"}])
        self.assertEqual(
            [request.url for request in transport.requests],
            ["https://example.atlassian.net/wiki/api/v2/pages", "https://example.atlassian.net/wiki/api/v2/pages?cursor=next"])

    def test_rejects_an_invalid_json_response(self) -> None:
        transport = MockTransport([MockResponse(200, {}, b"{")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        with self.assertRaises(ResponseError):
            client.make_paginated_request("GET", "/pages")

    def test_normalizes_http_errors(self) -> None:
        cases = [
            (401, AuthenticationError), (403, AuthorizationError), (404, NotFoundError), (409, ConflictError), (412, ConflictError),
            (429, RateLimitError)]
        for status, error_type in cases:
            with self.subTest(status=status):
                transport = MockTransport([MockResponse(status, {}, b"")])
                client = APIClient("example.atlassian.net", "user", "token", transport=transport)

                with self.assertRaises(error_type):
                    client.make_request("GET", "/pages")


# vim: set ts=4 sw=4 et tw=132:
