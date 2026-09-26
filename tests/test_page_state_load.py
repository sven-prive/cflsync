# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for page-state cache loading and validation."""

import json
import unittest

from cflsync import PageState, StateError
from tests.support import example_page_state, temporary_workarea


class TestPageStateLoad(unittest.TestCase):

    def test_loads_a_valid_state_by_page_id(self) -> None:
        with temporary_workarea() as workarea:
            expected = example_page_state()
            workarea.cache_path("123456").write_text(json.dumps(expected.to_json()), encoding="utf-8")

            self.assertEqual(PageState.load(workarea.cache_path("123456")), expected)

    def test_rejects_malformed_json(self) -> None:
        with temporary_workarea() as workarea:
            workarea.cache_path("123456").write_text("{", encoding="utf-8")

            with self.assertRaises(StateError):
                PageState.load(workarea.cache_path("123456"))

    def test_rejects_an_unsupported_format(self) -> None:
        with temporary_workarea() as workarea:
            value = example_page_state().to_json()
            value["format"] = 1
            workarea.cache_path("123456").write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(StateError):
                PageState.load(workarea.cache_path("123456"))

    def test_ignores_unknown_fields(self) -> None:
        with temporary_workarea() as workarea:
            expected = example_page_state()
            value = expected.to_json()
            value["future"] = {"field": "value"}
            value["page"]["future"] = True
            value["attachments"]["diagram.png"]["future"] = ["value"]
            workarea.cache_path("123456").write_text(json.dumps(value), encoding="utf-8")

            self.assertEqual(PageState.load(workarea.cache_path("123456")), expected)

    def test_rejects_a_null_required_page_field(self) -> None:
        with temporary_workarea() as workarea:
            value = example_page_state().to_json()
            value["page"]["title"] = None
            workarea.cache_path("123456").write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaises(StateError):
                PageState.load(workarea.cache_path("123456"))

    def test_rejects_an_invalid_page_id(self) -> None:
        with temporary_workarea() as workarea:
            with self.assertRaises(StateError):
                PageState.load(workarea.cache_dir / "not-an-id.json")

    def test_rejects_a_cache_filename_that_disagrees_with_page_id(self) -> None:
        with temporary_workarea() as workarea:
            workarea.cache_path("123456").write_text(json.dumps(example_page_state("654321").to_json()), encoding="utf-8")

            with self.assertRaises(StateError):
                PageState.load(workarea.cache_path("123456"))


# vim: set ts=4 sw=4 et tw=132:
