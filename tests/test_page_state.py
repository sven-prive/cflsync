# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for typed page synchronization state."""

import hashlib
import unittest

from cflsync import AttachmentMetadata, PageMetadata, PageState, StateError


class TestPageStateSerialization(unittest.TestCase):

    def test_metadata_constructor_rejects_an_invalid_content_hash(self) -> None:
        with self.assertRaises(StateError):
            PageMetadata(id="123456", title="Example page", directory="Example page", version=17, content_hash="not-a-hash")

    def test_serializes_the_format_1_state_shape(self) -> None:
        page_hash = hashlib.sha256(b"page").hexdigest()
        attachment_hash = hashlib.sha256(b"attachment").hexdigest()
        state = PageState(
            page=PageMetadata(id="123456", title="Example page", directory="Example page", version=17, content_hash=page_hash),
            attachments={"diagram.png": AttachmentMetadata(id="987654", version=3, content_hash=attachment_hash)})

        self.assertEqual(
            state.to_json(), {
                "format": 1,
                "page": {
                    "id": "123456",
                    "title": "Example page",
                    "directory": "Example page",
                    "version": 17,
                    "content_hash": page_hash},
                "attachments": {
                    "diagram.png": {
                        "id": "987654",
                        "version": 3,
                        "content_hash": attachment_hash}}})


# vim: set ts=4 sw=4 et tw=132:
