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

    def test_extra_metadata_does_not_block_supported_nodes(self) -> None:
        text = {"type": "text", "text": "Synthetic", "extra": 1}
        paragraph = {"type": "paragraph", "content": [text], "attrs": {"alignment": "center"}}
        item = {"type": "listItem", "content": [paragraph], "attrs": {"editorState": "unused"}}
        cases = [
            (paragraph, "Para"),
            ({"type": "heading", "attrs": {"level": 3}, "content": [text]}, "Header"),
            ({"type": "blockquote", "content": [paragraph]}, "BlockQuote"),
            ({"type": "bulletList", "content": [item]}, "BulletList"),
            ({"type": "orderedList", "attrs": {"order": 4}, "content": [item]}, "OrderedList"),
            ({"type": "codeBlock", "attrs": {"language": "python"}, "content": [text]}, "CodeBlock"),
            ({"type": "rule"}, "HorizontalRule"),
        ]
        for node, expected_type in cases:
            with self.subTest(node_type=node["type"]):
                node.setdefault("attrs", {})["futureMetadata"] = {"value": 42}
                node["extra"] = True
                document = {"type": "doc", "version": 1, "content": [node], "metadata": {"revision": 7}}
                before = json.dumps(document, sort_keys=True)
                pandoc = RecordingPandoc()
                ADFToMarkdownConverter(pandoc).convert(document)

                self.assertEqual(pandoc.pandoc["blocks"][0]["t"], expected_type)
                self.assertEqual(json.dumps(document, sort_keys=True), before)

    def test_unsupported_formatting_keeps_text_and_supported_marks(self) -> None:
        pandoc = RecordingPandoc()
        text = {
            "type": "text", "text": "Synthetic", "marks": [
                {"type": "textColor", "attrs": {"color": "#123456"}},
                {"type": "underline"},
                {"type": "code", "extra": True},
                {"type": "strong", "attrs": {"future": 1}},
                {"type": "link", "attrs": {"href": "https://example.test", "future": 2}},
            ]}
        document = {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [text, {"type": "hardBreak", "attrs": {"future": 3}}]}]}
        ADFToMarkdownConverter(pandoc).convert(document)

        inlines = pandoc.pandoc["blocks"][0]["c"]
        self.assertEqual(inlines[0]["t"], "Link")
        self.assertEqual(inlines[0]["c"][2], ["https://example.test", ""])
        strong = inlines[0]["c"][1][0]
        self.assertEqual(strong["t"], "Strong")
        self.assertEqual(strong["c"], [{"t": "Code", "c": [["", [], []], "Synthetic"]}])
        self.assertEqual(inlines[1], {"t": "LineBreak"})

    def test_structures_and_invalid_required_values_retain_original_json(self) -> None:
        paragraph = {"type": "paragraph", "content": [{"type": "text", "text": "Cell"}]}
        nodes = [
            {"type": "extension", "attrs": {"extensionKey": "synthetic", "metadata": {"value": 42}}},
            {"type": "table", "attrs": {"layout": "wide"}, "content": [
                {"type": "tableRow", "content": [
                    {"type": "tableCell", "attrs": {"colspan": 2}, "content": [paragraph, paragraph]}]}]},
            {"type": "heading", "attrs": {"level": "invalid"}, "content": []},
            {"type": "orderedList", "attrs": {"order": 0}, "content": []},
            {"type": "paragraph", "content": [{"type": "text", "text": "Invalid link", "marks": [
                {"type": "link", "attrs": {"href": 42}}]}]},
        ]
        for node in nodes:
            with self.subTest(node_type=node["type"]):
                pandoc = RecordingPandoc()
                ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [node]})
                block = pandoc.pandoc["blocks"][0]

                self.assertEqual(block["t"], "CodeBlock")
                self.assertEqual(block["c"][0], ["", ["atlas_doc_format"], []])
                self.assertEqual(json.loads(block["c"][1]), node)

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
