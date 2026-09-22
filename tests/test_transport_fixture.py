# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for mock API transport fixtures."""

import json
import unittest

from tests.support import MockResponse, MockTransport, RecordedRequest


class TestMockTransport(unittest.TestCase):

    def test_records_a_request_and_returns_the_queued_response(self) -> None:
        response = MockResponse.from_json({"id": "123"})
        transport = MockTransport(iter([response]))

        actual_response = transport.request(
            "GET", "https://example.atlassian.net/wiki/api/v2/pages/123", headers={"Accept": "application/json"})

        self.assertEqual(actual_response, response)
        self.assertEqual(json.loads(actual_response.body), {"id": "123"})
        self.assertEqual(
            transport.requests, [
                RecordedRequest(
                    method="GET",
                    url="https://example.atlassian.net/wiki/api/v2/pages/123",
                    headers={"Accept": "application/json"},
                    body=None)])


# vim: set ts=4 sw=4 et tw=132:
