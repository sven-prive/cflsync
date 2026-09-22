# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Reusable fixtures for cflsync tests."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cflsync import AttachmentMetadata, PageMetadata, PageState, Workarea


@contextmanager
def temporary_workarea(profile: str = "default") -> Iterator[Workarea]:
    """Yield an initialized workarea rooted in a directory removed on exit."""
    with TemporaryDirectory(prefix="cflsync-test-") as temporary_dir:
        root = Path(temporary_dir)
        yield Workarea.init(root, profile)


def example_page_state(
    page_id: str = "123456",
    title: str = "Example page",
    directory: str = "Example page",
) -> PageState:
    """Return a valid format-1 state for tests that need persisted state."""
    return PageState(
        page=PageMetadata(
            id=page_id,
            title=title,
            directory=directory,
            version=17,
            content_hash=hashlib.sha256(b"page").hexdigest(),
        ),
        attachments={
            "diagram.png": AttachmentMetadata(
                id="987654",
                version=3,
                content_hash=hashlib.sha256(b"attachment").hexdigest(),
            )
        },
    )


@dataclass(frozen=True)
class RecordedRequest:
    """A request received by :class:`MockTransport`."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None


@dataclass(frozen=True)
class MockResponse:
    """A response returned by :class:`MockTransport`."""

    status: int
    headers: dict[str, str]
    body: bytes

    @classmethod
    def from_json(cls, value: object, status: int = 200) -> "MockResponse":
        return cls(
            status=status,
            headers={"Content-Type": "application/json"},
            body=json.dumps(value).encode(),
        )


class MockTransport:
    """Queue responses and record requests for API-client tests."""

    def __init__(self, responses: Iterable[MockResponse] = ()) -> None:
        self.requests: list[RecordedRequest] = []
        self._responses = deque(responses)

    def enqueue(self, response: MockResponse) -> None:
        self._responses.append(response)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
    ) -> MockResponse:
        self.requests.append(
            RecordedRequest(method, url, dict(headers or {}), body)
        )
        if not self._responses:
            raise AssertionError(f"unexpected request: {method} {url}")

        return self._responses.popleft()
