# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for ADF to Markdown conversion."""

import json
import unittest

from cflsync import ADFToMarkdownConverter, PandocRunner


class RecordingPandoc:

    def pandoc_to_gfm(self, pandoc):
        self.pandoc = pandoc
        return "converted\n"


class TestADFToMarkdownConverter(unittest.TestCase):

    def test_maps_direct_blocks_and_inlines(self) -> None:
        pandoc = RecordingPandoc()
        converter = ADFToMarkdownConverter(pandoc)
        document = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type": "paragraph",
                    "content": [{
                        "type": "text",
                        "text": "plain text"}]}, {
                            "type": "heading",
                            "attrs": {
                                "level": 2},
                            "content": [{
                                "type": "text",
                                "text": "Heading"}]}, {
                                    "type": "blockquote",
                                    "content": [{
                                        "type": "paragraph",
                                        "content": [{
                                            "type": "text",
                                            "text": "Quote"}]}]},
                {
                    "type": "bulletList",
                    "content":
                    [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Item"}]}]}]}, {
                                    "type": "codeBlock",
                                    "attrs": {
                                        "language": "python"},
                                    "content": [{
                                        "type": "text",
                                        "text": "print(1)"}]}, {
                                            "type": "rule"}, ], }

        self.assertEqual(converter.convert(document), "converted\n")
        self.assertEqual(
            pandoc.pandoc, {
                "pandoc-api-version":
                list(PandocRunner.API_VERSION),
                "meta": {},
                "blocks": [
                    {
                        "t": "Para",
                        "c": [{
                            "t": "Str",
                            "c": "plain"}, {
                                "t": "Space"}, {
                                    "t": "Str",
                                    "c": "text"}]}, {
                                        "t": "Header",
                                        "c": [2, ["", [], []], [{
                                            "t": "Str",
                                            "c": "Heading"}]]}, {
                                                "t": "BlockQuote",
                                                "c": [{
                                                    "t": "Para",
                                                    "c": [{
                                                        "t": "Str",
                                                        "c": "Quote"}]}]}, {
                                                            "t": "BulletList",
                                                            "c": [[{
                                                                "t": "Plain",
                                                                "c": [{
                                                                    "t": "Str",
                                                                    "c": "Item"}]}]]}, {
                                                                        "t": "CodeBlock",
                                                                        "c": [["", ["python"], []], "print(1)"]}, {
                                                                            "t": "HorizontalRule"}, ], })

    def test_retains_an_unsupported_inline_in_its_enclosing_block(self) -> None:
        pandoc = RecordingPandoc()
        paragraph = {"type": "paragraph", "content": [{"type": "status", "attrs": {"text": "Done", "color": "green"}}]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [paragraph]})

        self.assertEqual(
            pandoc.pandoc["blocks"][0], {
                "t": "CodeBlock",
                "c": [["", ["atlas_doc_format"], []],
                      json.dumps(paragraph, sort_keys=True, separators=(",", ":"))], })


# vim: set ts=4 sw=4 et tw=132:
