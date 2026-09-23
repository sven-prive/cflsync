# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for API client transport behavior."""

import json
import unittest

from cflsync import APIClient, APIError, APIResponse, TransportError
from tests.support import MockResponse, MockTransport


class TestAPIClientTransport(unittest.TestCase):

    def test_uses_the_injected_transport(self) -> None:
        response = MockResponse.from_json({"id": "123456"})
        transport = MockTransport([response])
        client = APIClient("example.atlassian.net", "user@example.test", "token", transport=transport)

        actual = client.make_request("GET")
        self.assertIsInstance(actual, APIResponse)
        self.assertIsNot(actual, response)
        self.assertEqual(actual.body, response.body)
        self.assertEqual(len(transport.requests), 1)

    def test_encodes_a_json_request(self) -> None:
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
        self.assertEqual(request.method, "PUT")
        self.assertEqual(request.path, "/pages/A page")
        self.assertEqual(request.parameters, {"expand": "body.atlas_doc_format", "title": "A page"})
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
        self.assertEqual([request.path for request in transport.requests], ["/pages", "/wiki/api/v2/pages?cursor=next"])
        self.assertEqual(transport.clone_prefixes, [""])

    def test_rejects_an_invalid_json_response(self) -> None:
        transport = MockTransport([MockResponse(200, {}, b"{")])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        with self.assertRaises(APIError) as raised:
            client.make_paginated_request("GET", "/pages")
        self.assertIsNone(raised.exception.status)
        self.assertIn("valid JSON", str(raised.exception))

    def test_raises_an_api_error_for_http_errors(self) -> None:
        cases = [401, 403, 404, 409, 412, 429]
        for status in cases:
            with self.subTest(status=status):
                transport = MockTransport([MockResponse(status, {}, b"")])
                client = APIClient("example.atlassian.net", "user", "token", transport=transport, read_attempts=1)

                with self.assertRaises(APIError) as raised:
                    client.make_request("GET", "/pages")
                self.assertEqual(raised.exception.status, status)

    def test_retries_transient_read_failures(self) -> None:
        transport = MockTransport([MockResponse(503, {}, b""), MockResponse.from_json({"id": "123456"})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        response = client.make_request("GET", "/pages/123456")

        self.assertEqual(response.body, b'{"id": "123456"}')
        self.assertEqual(len(transport.requests), 2)

    def test_retries_transient_transport_failures_for_reads(self) -> None:

        class FlakyTransport:

            def __init__(self) -> None:
                self.calls = 0

            def clone(self, prefix=None):
                return self

            def make_request(self, method, path="", parameters=None, headers=None, body=None):
                self.calls += 1
                if self.calls == 1:
                    raise TransportError("injected connection failure")

                return MockResponse.from_json({"id": "123456"})

        transport = FlakyTransport()
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        self.assertEqual(client.make_request("GET", "/pages/123456").status, 200)
        self.assertEqual(transport.calls, 2)

    def test_never_retries_writes(self) -> None:
        transport = MockTransport([MockResponse(503, {}, b""), MockResponse.from_json({})])
        client = APIClient("example.atlassian.net", "user", "token", transport=transport)

        with self.assertRaises(APIError) as raised:
            client.make_request("PUT", "/pages/123456", body=b"{}")

        self.assertEqual(raised.exception.status, 503)
        self.assertEqual(len(transport.requests), 1)

    def test_http_errors_explain_the_expected_remediation(self) -> None:
        cases = [(401, "credentials"), (403, "permissions"), (404, "not found"), (409, "changed")]
        for status, expected in cases:
            with self.subTest(status=status):
                transport = MockTransport([MockResponse(status, {}, b"")])
                client = APIClient("example.atlassian.net", "user", "token", transport=transport, read_attempts=1)

                with self.assertRaisesRegex(APIError, expected):
                    client.make_request("GET", "/pages")


# vim: set ts=4 sw=4 et tw=132:
