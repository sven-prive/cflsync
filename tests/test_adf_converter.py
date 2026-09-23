# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for ADF to Markdown conversion."""

import json
import unittest
from datetime import datetime

from cflsync import ADFToMarkdownConverter, MediaResolver, PandocRunner


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
            (paragraph, "Para"), ({
                "type": "heading",
                "attrs": {
                    "level": 3},
                "content": [text]}, "Header"), ({
                    "type": "blockquote",
                    "content": [paragraph]}, "BlockQuote"), ({
                        "type": "bulletList",
                        "content": [item]}, "BulletList"),
            ({
                "type": "orderedList",
                "attrs": {
                    "order": 4},
                "content": [item]}, "OrderedList"),
            ({
                "type": "codeBlock",
                "attrs": {
                    "language": "python"},
                "content": [text]}, "CodeBlock"), ({
                    "type": "rule"}, "HorizontalRule"), ]
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
            "type":
            "text",
            "text":
            "Synthetic",
            "marks": [
                {
                    "type": "textColor",
                    "attrs": {
                        "color": "#123456"}}, {
                            "type": "underline"}, {
                                "type": "code",
                                "extra": True}, {
                                    "type": "strong",
                                    "attrs": {
                                        "future": 1}}, {
                                            "type": "link",
                                            "attrs": {
                                                "href": "https://example.test",
                                                "future": 2}}, ]}
        document = {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [text, {
                    "type": "hardBreak",
                    "attrs": {
                        "future": 3}}]}]}
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
            {
                "type": "extension",
                "attrs": {
                    "extensionKey": "synthetic",
                    "metadata": {
                        "value": 42}}}, {
                            "type": "panel",
                            "attrs": {
                                "panelType": "info"},
                            "content": [paragraph]}, {
                                "type": "heading",
                                "attrs": {
                                    "level": "invalid"},
                                "content": []}, {
                                    "type": "orderedList",
                                    "attrs": {
                                        "order": 0},
                                    "content": []},
            {
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "Invalid link",
                    "marks": [{
                        "type": "link",
                        "attrs": {
                            "href": 42}}]}]}, ]
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

    def test_maps_a_table_to_a_pandoc_table(self) -> None:
        pandoc = RecordingPandoc()
        paragraph = {"type": "paragraph", "content": [{"type": "text", "text": "Cell"}]}
        table = {
            "type":
            "table",
            "attrs": {
                "layout": "wide",
                "width": 760.0},
            "content": [
                {
                    "type": "tableRow",
                    "content": [{
                        "type": "tableHeader",
                        "attrs": {
                            "colspan": 2,
                            "rowspan": 1},
                        "content": [paragraph]}]}, {
                            "type": "tableRow",
                            "content":
                            [{
                                "type": "tableCell",
                                "content": [paragraph]}, {
                                    "type": "tableCell",
                                    "content": [paragraph]}]}]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [table]})

        block = pandoc.pandoc["blocks"][0]
        attributes, caption, colspecs, head, bodies, foot = block["c"]
        self.assertEqual(block["t"], "Table")
        self.assertEqual([attributes, caption, foot], [["", [], []], [None, []], [["", [], []], []]])
        self.assertEqual(len(colspecs), 2)
        self.assertEqual(head[1][0][1][0][2:4], [1, 2])
        self.assertEqual(bodies[0][1:3], [0, []])
        self.assertEqual(len(bodies[0][3][0][1]), 2)

    def test_retains_a_structurally_invalid_table(self) -> None:
        pandoc = RecordingPandoc()
        table = {"type": "table", "content": [{"type": "tableRow", "content": [{"type": "paragraph", "content": []}]}]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [table]})

        self.assertEqual(pandoc.pandoc["blocks"][0]["c"][0], ["", ["atlas_doc_format"], []])

    def test_maps_media_resolved_through_the_attachment_manifest(self) -> None:
        media = MediaResolver([("diagram.png", "file-1"), ("report.pdf", "file-2")])
        pandoc = RecordingPandoc()
        document = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type":
                    "mediaSingle",
                    "attrs": {
                        "layout": "center",
                        "width": 474},
                    "content": [
                        {
                            "type": "media",
                            "attrs": {
                                "type": "file",
                                "id": "file-1",
                                "collection": "contentId-123456",
                                "alt": "A diagram"}}]},
                {
                    "type": "mediaGroup",
                    "content": [{
                        "type": "media",
                        "attrs": {
                            "type": "file",
                            "id": "file-2",
                            "collection": "contentId-123456"}}]}]}

        ADFToMarkdownConverter(pandoc, media).convert(document)

        image, attached_file = pandoc.pandoc["blocks"]
        self.assertEqual(
            image, {
                "t":
                "Para",
                "c": [
                    {
                        "t":
                        "Image",
                        "c": [
                            ["", [], []], [{
                                "t": "Str",
                                "c": "A"}, {
                                    "t": "Space"}, {
                                        "t": "Str",
                                        "c": "diagram"}], ["_attachments/diagram.png", ""]]}]})
        self.assertEqual(
            attached_file, {
                "t": "Para",
                "c": [{
                    "t": "Link",
                    "c": [["", [], []], [{
                        "t": "Str",
                        "c": "report.pdf"}], ["_attachments/report.pdf", ""]]}]})

    def test_maps_external_media_without_a_manifest(self) -> None:
        pandoc = RecordingPandoc()
        media = {"type": "media", "attrs": {"type": "external", "url": "https://example.test/logo.png"}}
        document = {"type": "doc", "version": 1, "content": [{"type": "mediaSingle", "content": [media]}]}

        ADFToMarkdownConverter(pandoc).convert(document)

        self.assertEqual(pandoc.pandoc["blocks"][0]["c"][0]["t"], "Image")

    def test_retains_media_that_the_manifest_cannot_resolve(self) -> None:
        pandoc = RecordingPandoc()
        media = {"type": "media", "attrs": {"type": "file", "id": "file-9", "collection": "contentId-123456"}}
        node = {"type": "mediaSingle", "content": [media]}

        ADFToMarkdownConverter(pandoc, MediaResolver([
            ("diagram.png", "file-1")])).convert({
                "type": "doc",
                "version": 1,
                "content": [node]})

        self.assertEqual(
            pandoc.pandoc["blocks"][0], {
                "t": "CodeBlock",
                "c": [["", ["atlas_doc_format"], []],
                      json.dumps(node, sort_keys=True, separators=(",", ":"))], })

    def test_maps_an_emoji_to_its_unicode_text(self) -> None:
        pandoc = RecordingPandoc()
        emoji = {"type": "emoji", "attrs": {"shortName": ":smile:", "id": "1f604", "text": "\U0001F604"}}
        paragraph = {"type": "paragraph", "content": [{"type": "text", "text": "Nice"}, emoji]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [paragraph]})

        self.assertEqual(
            pandoc.pandoc["blocks"][0], {
                "t": "Para",
                "c": [{
                    "t": "Str",
                    "c": "Nice"}, {
                        "t": "Str",
                        "c": "\U0001F604"}]})

    def test_maps_a_date_to_a_raw_html_span(self) -> None:
        pandoc = RecordingPandoc()
        timestamp = "1775001600000"
        expected = datetime.fromtimestamp(int(timestamp) / 1000).date().isoformat()
        date = {"type": "date", "attrs": {"timestamp": timestamp}}

        ADFToMarkdownConverter(pandoc).convert(
            {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [date]}], })

        self.assertEqual(
            pandoc.pandoc["blocks"][0]["c"], [
                {
                    "t": "RawInline",
                    "c": ["html", f'<span cflsync-type="date" cflsync-timestamp="{timestamp}">']}, {
                        "t": "Str",
                        "c": expected}, {
                            "t": "RawInline",
                            "c": ["html", "</span>"]}])

    def test_maps_a_status_to_a_raw_html_span(self) -> None:
        pandoc = RecordingPandoc()
        status = {"type": "status", "attrs": {"text": "Done & ready", "color": "neutral"}}

        ADFToMarkdownConverter(pandoc).convert(
            {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [status]}], })

        self.assertEqual(
            pandoc.pandoc["blocks"][0]["c"], [
                {
                    "t": "RawInline",
                    "c": ["html", '<span cflsync-type="status" style="background-color: gray">']}, {
                        "t": "Str",
                        "c": "Done"}, {
                            "t": "Space"}, {
                                "t": "Str",
                                "c": "&"}, {
                                    "t": "Space"}, {
                                        "t": "Str",
                                        "c": "ready"}, {
                                            "t": "RawInline",
                                            "c": ["html", "</span>"]}])

    def test_maps_a_mention_to_a_raw_html_span(self) -> None:
        pandoc = RecordingPandoc()
        mention = {
            "type": "mention",
            "attrs": {
                "id": "account-123",
                "text": "@Example User",
                "accessLevel": "SITE",
                "userType": "DEFAULT"}}

        ADFToMarkdownConverter(pandoc).convert(
            {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [mention]}]})

        self.assertEqual(
            pandoc.pandoc["blocks"][0]["c"], [
                {
                    "t":
                    "RawInline",
                    "c": [
                        "html",
                        '<span cflsync-type="mention" cflsync-id="account-123" cflsync-access-level="SITE" cflsync-user-type="DEFAULT">'
                    ]}, {
                        "t": "Str",
                        "c": "@Example"}, {
                            "t": "Space"}, {
                                "t": "Str",
                                "c": "User"}, {
                                    "t": "RawInline",
                                    "c": ["html", "</span>"]}])

    def test_retains_a_custom_emoji_without_unicode_text(self) -> None:
        pandoc = RecordingPandoc()
        emoji = {"type": "emoji", "attrs": {"shortName": ":atlassian:", "id": "atlassian-check"}}
        paragraph = {"type": "paragraph", "content": [emoji]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [paragraph]})

        block = pandoc.pandoc["blocks"][0]
        self.assertEqual(block["c"][0], ["", ["atlas_doc_format"], []])
        self.assertEqual(json.loads(block["c"][1]), paragraph)

    def test_retains_an_unsupported_inline_in_its_enclosing_block(self) -> None:
        pandoc = RecordingPandoc()
        paragraph = {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": "https://example.test"}}]}

        ADFToMarkdownConverter(pandoc).convert({"type": "doc", "version": 1, "content": [paragraph]})

        self.assertEqual(
            pandoc.pandoc["blocks"][0], {
                "t": "CodeBlock",
                "c": [["", ["atlas_doc_format"], []],
                      json.dumps(paragraph, sort_keys=True, separators=(",", ":"))], })


# vim: set ts=4 sw=4 et tw=132:
