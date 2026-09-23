# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for Markdown to ADF conversion."""

import json
import unittest
from datetime import datetime

from cflsync import ADFToMarkdownConverter, ConversionError, MarkdownToADFConverter, MediaResolver, PandocRunner


class RecordingPandoc:

    def __init__(self, pandoc):
        self.pandoc = pandoc
        self.markdown = None

    def gfm_to_pandoc(self, markdown):
        self.markdown = markdown
        return self.pandoc


class PandocBridge:

    def pandoc_to_gfm(self, pandoc):
        self.pandoc = pandoc
        return "source"

    def gfm_to_pandoc(self, markdown):
        return self.pandoc


def _cell(cell_type, blocks, colspan=1, rowspan=1):
    return {"type": cell_type, "attrs": {"colspan": colspan, "rowspan": rowspan}, "content": blocks}


def pandoc_document(blocks):
    return {"pandoc-api-version": list(PandocRunner.API_VERSION), "meta": {}, "blocks": blocks}


class TestMarkdownToADFConverter(unittest.TestCase):

    def test_maps_direct_blocks_and_inlines(self) -> None:
        pandoc = RecordingPandoc(
            pandoc_document(
                [
                    {
                        "t":
                        "Para",
                        "c": [
                            {
                                "t": "Str",
                                "c": "plain"}, {
                                    "t": "Space"}, {
                                        "t": "Str",
                                        "c": "text"}, {
                                            "t": "Strong",
                                            "c": [{
                                                "t": "Str",
                                                "c": "strong"}]}, {
                                                    "t": "Emph",
                                                    "c": [{
                                                        "t": "Str",
                                                        "c": "emphasis"}]}, {
                                                            "t": "Strikeout",
                                                            "c": [{
                                                                "t": "Str",
                                                                "c": "struck"}]}, {
                                                                    "t": "Code",
                                                                    "c": [["", [], []], "code"]},
                            {
                                "t": "Link",
                                "c": [["", [], []], [{
                                    "t": "Str",
                                    "c": "link"}], ["https://example.test", ""]]}, {
                                        "t": "LineBreak"}, ], }, {
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
                                                                        "c": "Item"}]}]]},
                    {
                        "t": "OrderedList",
                        "c": [[3, {
                            "t": "Decimal"}, {
                                "t": "Period"}], [[{
                                    "t": "Plain",
                                    "c": [{
                                        "t": "Str",
                                        "c": "Third"}]}]]]}, {
                                            "t": "CodeBlock",
                                            "c": [["", ["python"], []], "print(1)"]}, {
                                                "t": "HorizontalRule"}, ]))

        document = MarkdownToADFConverter(pandoc).convert("source")

        self.assertEqual(pandoc.markdown, "source")
        self.assertEqual(
            document, {
                "type":
                "doc",
                "version":
                1,
                "content": [
                    {
                        "type":
                        "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "plain text"}, {
                                    "type": "text",
                                    "text": "strong",
                                    "marks": [{
                                        "type": "strong"}]}, {
                                            "type": "text",
                                            "text": "emphasis",
                                            "marks": [{
                                                "type": "em"}]}, {
                                                    "type": "text",
                                                    "text": "struck",
                                                    "marks": [{
                                                        "type": "strike"}]}, {
                                                            "type": "text",
                                                            "text": "code",
                                                            "marks": [{
                                                                "type": "code"}]},
                            {
                                "type": "text",
                                "text": "link",
                                "marks": [{
                                    "type": "link",
                                    "attrs": {
                                        "href": "https://example.test",
                                        "title": ""}}], }, {
                                            "type": "hardBreak"}, ], }, {
                                                "type": "heading",
                                                "attrs": {
                                                    "level": 2},
                                                "content": [{
                                                    "type": "text",
                                                    "text": "Heading"}]},
                    {
                        "type": "blockquote",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Quote"}]}]}, {
                                    "type":
                                    "bulletList",
                                    "content": [
                                        {
                                            "type": "listItem",
                                            "content": [{
                                                "type": "paragraph",
                                                "content": [{
                                                    "type": "text",
                                                    "text": "Item"}]}]}],
                                }, {
                                    "type":
                                    "orderedList",
                                    "attrs": {
                                        "order": 3},
                                    "content": [
                                        {
                                            "type": "listItem",
                                            "content": [{
                                                "type": "paragraph",
                                                "content": [{
                                                    "type": "text",
                                                    "text": "Third"}]}]}], }, {
                                                        "type": "codeBlock",
                                                        "attrs": {
                                                            "language": "python"},
                                                        "content": [{
                                                            "type": "text",
                                                            "text": "print(1)"}]}, {
                                                                "type": "rule"}, ], },
        )

    def test_decodes_an_opaque_marker(self) -> None:
        node = {
            "type": "panel",
            "attrs": {
                "panelType": "info"},
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "Info"}]}], }
        pandoc = RecordingPandoc(pandoc_document([{"t": "CodeBlock", "c": [["", ["atlas_doc_format"], []], json.dumps(node)]}]))

        document = MarkdownToADFConverter(pandoc).convert("source")

        self.assertEqual(document["content"], [node])

    def test_round_trips_supported_adf(self) -> None:
        source = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type":
                    "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Text"}, {
                                "type": "text",
                                "text": "strong",
                                "marks": [{
                                    "type": "strong"}]}, {
                                        "type": "text",
                                        "text": "code",
                                        "marks": [{
                                            "type": "code"}]}, {
                                                "type": "hardBreak"}, ], }, {
                                                    "type": "heading",
                                                    "attrs": {
                                                        "level": 2},
                                                    "content": [{
                                                        "type": "text",
                                                        "text": "Heading"}]}, {
                                                            "type": "blockquote",
                                                            "content":
                                                            [{
                                                                "type": "paragraph",
                                                                "content": [{
                                                                    "type": "text",
                                                                    "text": "Quote"}]}]},
                {
                    "type":
                    "bulletList",
                    "content":
                    [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Item"}]}]}], },
                {
                    "type":
                    "orderedList",
                    "attrs": {
                        "order": 3},
                    "content":
                    [{
                        "type": "listItem",
                        "content": [{
                            "type": "paragraph",
                            "content": [{
                                "type": "text",
                                "text": "Third"}]}]}], }, {
                                    "type": "codeBlock",
                                    "attrs": {
                                        "language": "python"},
                                    "content": [{
                                        "type": "text",
                                        "text": "print(1)"}]}, {
                                            "type": "rule"}, ], }
        pandoc = PandocBridge()

        markdown = ADFToMarkdownConverter(pandoc).convert(source)
        document = MarkdownToADFConverter(pandoc).convert(markdown)

        self.assertEqual(document, source)

    def test_round_trips_tables_through_pipe_and_html_representations(self) -> None:
        pandoc = PandocRunner()
        forward = ADFToMarkdownConverter(pandoc)
        reverse = MarkdownToADFConverter(pandoc)
        paragraph = {"type": "paragraph", "content": [{"type": "text", "text": "Cell"}]}
        simple = {
            "type":
            "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [_cell("tableHeader", [paragraph])]}, {
                        "type": "tableRow",
                        "content": [_cell("tableCell", [paragraph])]}]}
        complex_table = {
            "type":
            "table",
            "content": [
                {
                    "type": "tableRow",
                    "content": [_cell("tableHeader", [paragraph], colspan=2)]}, {
                        "type": "tableRow",
                        "content": [_cell("tableCell", [paragraph, paragraph]),
                                    _cell("tableCell", [paragraph])]}]}

        for name, table in (("pipe", simple), ("html", complex_table)):
            with self.subTest(representation=name):
                source = {"type": "doc", "version": 1, "content": [table]}
                markdown = forward.convert(source)

                self.assertEqual(markdown.lstrip().startswith("<table"), name == "html")
                self.assertEqual(reverse.convert(markdown), source)

    def test_accepts_the_heading_identifiers_that_reading_gfm_assigns(self) -> None:
        document = MarkdownToADFConverter(PandocRunner()).convert("## An L2 Heading\n\nText\n")

        self.assertEqual(
            document["content"][0], {
                "type": "heading",
                "attrs": {
                    "level": 2},
                "content": [{
                    "type": "text",
                    "text": "An L2 Heading"}]})

    def test_keeps_the_unicode_text_of_an_emoji_shortcode(self) -> None:
        document = MarkdownToADFConverter(PandocRunner()).convert("Nice :smile: day\n")

        self.assertEqual(
            document["content"][0], {
                "type": "paragraph",
                "content": [{
                    "type": "text",
                    "text": "Nice \U0001F604 day"}]})

    def test_maps_a_raw_html_status_through_pandoc(self) -> None:
        document = MarkdownToADFConverter(PandocRunner()).convert(
            'Before <span cflsync-type="status" style="background-color: green">Done &amp; ready</span> after\n')

        self.assertEqual(
            document["content"][0], {
                "type":
                "paragraph",
                "content": [
                    {
                        "type": "text",
                        "text": "Before "}, {
                            "type": "status",
                            "attrs": {
                                "text": "Done & ready",
                                "color": "green"}}, {
                                    "type": "text",
                                    "text": " after"}]})

    def test_round_trips_a_status(self) -> None:
        source = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type": "paragraph",
                    "content":
                    [{
                        "type": "text",
                        "text": "State: "}, {
                            "type": "status",
                            "attrs": {
                                "text": "Done",
                                "color": "green"}}]}]}
        pandoc = PandocRunner()
        markdown = ADFToMarkdownConverter(pandoc).convert(source)
        document = MarkdownToADFConverter(pandoc).convert(markdown)

        self.assertEqual(document, source)

    def test_maps_a_raw_html_date_through_pandoc(self) -> None:
        timestamp = "1775001600000"
        date = datetime.fromtimestamp(int(timestamp) / 1000).date().isoformat()
        document = MarkdownToADFConverter(
            PandocRunner()).convert(f'<span cflsync-type="date" cflsync-timestamp="{timestamp}">{date}</span>\n')

        self.assertEqual(
            document["content"][0], {
                "type": "paragraph",
                "content": [{
                    "type": "date",
                    "attrs": {
                        "timestamp": timestamp}}]})

    def test_round_trips_a_date(self) -> None:
        source = {
            "type": "doc",
            "version": 1,
            "content": [{
                "type": "paragraph",
                "content": [{
                    "type": "date",
                    "attrs": {
                        "timestamp": "1775001600000"}}]}]}
        pandoc = PandocRunner()
        markdown = ADFToMarkdownConverter(pandoc).convert(source)
        document = MarkdownToADFConverter(pandoc).convert(markdown)

        self.assertEqual(document, source)

    def test_maps_a_raw_html_mention_through_pandoc(self) -> None:
        document = MarkdownToADFConverter(PandocRunner()).convert(
            '<span cflsync-type="mention" cflsync-id="account-123" cflsync-access-level="SITE" '
            'cflsync-user-type="DEFAULT">@Example User</span>\n')

        self.assertEqual(
            document["content"][0], {
                "type":
                "paragraph",
                "content": [
                    {
                        "type": "mention",
                        "attrs": {
                            "id": "account-123",
                            "text": "@Example User",
                            "accessLevel": "SITE",
                            "userType": "DEFAULT"}}]})

    def test_round_trips_a_mention(self) -> None:
        source = {
            "type":
            "doc",
            "version":
            1,
            "content":
            [{
                "type": "paragraph",
                "content": [{
                    "type": "mention",
                    "attrs": {
                        "id": "account-123",
                        "text": "@Example User"}}]}]}
        pandoc = PandocRunner()
        markdown = ADFToMarkdownConverter(pandoc).convert(source)
        document = MarkdownToADFConverter(pandoc).convert(markdown)

        self.assertEqual(document, source)

    def test_rejects_a_mention_without_an_account_id(self) -> None:
        with self.assertRaisesRegex(ConversionError, "non-empty account ID"):
            MarkdownToADFConverter(PandocRunner()).convert('<span cflsync-type="mention">@Example User</span>\n')

    def test_rejects_a_date_with_text_that_does_not_match_its_timestamp(self) -> None:
        with self.assertRaisesRegex(ConversionError, "must match"):
            MarkdownToADFConverter(
                PandocRunner()).convert('<span cflsync-type="date" cflsync-timestamp="1775001600000">2000-01-01</span>\n')

    def test_rejects_a_status_with_an_unsupported_css_color(self) -> None:
        with self.assertRaisesRegex(ConversionError, "unsupported attributes"):
            MarkdownToADFConverter(
                PandocRunner()).convert('<span cflsync-type="status" style="background-color: orange">Done</span>\n')

    def test_rejects_a_span_that_is_not_an_emoji(self) -> None:
        pandoc = RecordingPandoc(
            pandoc_document([{
                "t": "Para",
                "c": [{
                    "t": "Span",
                    "c": [["", ["footnote"], []], [{
                        "t": "Str",
                        "c": "text"}]]}]}]))

        with self.assertRaisesRegex(ConversionError, "emoji spans"):
            MarkdownToADFConverter(pandoc).convert("source")

    def test_rejects_raw_html_that_is_not_a_table(self) -> None:
        pandoc = RecordingPandoc(pandoc_document([{"t": "RawBlock", "c": ["html", "<div>text</div>"]}]))

        with self.assertRaisesRegex(ConversionError, "HTML table"):
            MarkdownToADFConverter(pandoc).convert("source")

    def test_round_trips_media_through_the_attachment_manifest(self) -> None:
        media = MediaResolver([("diagram.png", "file-1"), ("report.pdf", "file-2")])
        source = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type":
                    "mediaSingle",
                    "attrs": {
                        "layout": "center"},
                    "content": [
                        {
                            "type": "media",
                            "attrs": {
                                "type": "file",
                                "id": "file-1",
                                "collection": "contentId-123456",
                                "alt": "A diagram"}}]},
                {
                    "type":
                    "mediaSingle",
                    "attrs": {
                        "layout": "center"},
                    "content":
                    [{
                        "type": "media",
                        "attrs": {
                            "type": "external",
                            "url": "https://example.test/logo.png",
                            "alt": "logo.png"}}]}]}
        pandoc = PandocBridge()

        markdown = ADFToMarkdownConverter(pandoc, media).convert(source)
        document = MarkdownToADFConverter(pandoc, media, "contentId-123456").convert(markdown)

        self.assertEqual(document, source)

    def _mixed_paragraph(self, url):
        return RecordingPandoc(
            pandoc_document(
                [
                    {
                        "t": "Para",
                        "c": [{
                            "t": "Str",
                            "c": "text"}, {
                                "t": "Space"}, {
                                    "t": "Image",
                                    "c": [["", [], []], [], [url, ""]]}]}]))

    def test_maps_an_image_beside_other_content_to_inline_media(self) -> None:
        pandoc = self._mixed_paragraph("_attachments/diagram.png")

        document = MarkdownToADFConverter(pandoc, MediaResolver([("diagram.png", "file-1")]), "contentId-123456").convert("source")

        self.assertEqual(
            document["content"][0]["content"][1], {
                "type": "mediaInline",
                "attrs": {
                    "type": "file",
                    "id": "file-1",
                    "collection": "contentId-123456"}})

    def test_rejects_an_inline_image_outside_the_attachments_directory(self) -> None:
        pandoc = self._mixed_paragraph("https://example.test/logo.png")

        with self.assertRaisesRegex(ConversionError, "managed attachment"):
            MarkdownToADFConverter(pandoc, MediaResolver([("diagram.png", "file-1")]), "contentId-123456").convert("source")

    def test_round_trips_inline_media(self) -> None:
        pandoc = PandocRunner()
        media = MediaResolver([("diagram.png", "file-1")])
        source = {
            "type":
            "doc",
            "version":
            1,
            "content": [
                {
                    "type":
                    "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "Before "}, {
                                "type": "mediaInline",
                                "attrs": {
                                    "type": "file",
                                    "id": "file-1",
                                    "collection": "contentId-123456",
                                    "alt": "diagram.png"}}, {
                                        "type": "text",
                                        "text": " after."}]}]}

        markdown = ADFToMarkdownConverter(pandoc, media).convert(source)

        self.assertEqual(markdown, "Before ![diagram.png](_attachments/diagram.png) after.\n")
        self.assertEqual(MarkdownToADFConverter(pandoc, media, "contentId-123456").convert(markdown), source)

    def test_rejects_malformed_or_misplaced_opaque_markers(self) -> None:
        malformed = RecordingPandoc(pandoc_document([{"t": "CodeBlock", "c": [["", ["atlas_doc_format"], []], "not json"]}]))
        misplaced = RecordingPandoc(
            pandoc_document([{
                "t": "CodeBlock",
                "c": [["", ["atlas_doc_format"], []], '{"type":"text","text":"inline"}']}]))
        altered = RecordingPandoc(
            pandoc_document([{
                "t": "CodeBlock",
                "c": [["", ["atlas_doc_format", "python"], []], '{"type":"panel"}']}]))

        with self.assertRaisesRegex(ConversionError, "invalid JSON"):
            MarkdownToADFConverter(malformed).convert("source")

        with self.assertRaisesRegex(ConversionError, "block context"):
            MarkdownToADFConverter(misplaced).convert("source")

        with self.assertRaisesRegex(ConversionError, "unsupported attributes"):
            MarkdownToADFConverter(altered).convert("source")


# vim: set ts=4 sw=4 et tw=132:
