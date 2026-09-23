# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Tests for Markdown to ADF conversion."""

import json
import unittest

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

    def test_rejects_an_image_beside_other_paragraph_content(self) -> None:
        pandoc = RecordingPandoc(
            pandoc_document(
                [
                    {
                        "t":
                        "Para",
                        "c": [
                            {
                                "t": "Str",
                                "c": "text"}, {
                                    "t": "Space"}, {
                                        "t": "Image",
                                        "c": [["", [], []], [], ["_attachments/diagram.png", ""]]}]}]))

        with self.assertRaisesRegex(ConversionError, "only content"):
            MarkdownToADFConverter(pandoc, MediaResolver([("diagram.png", "file-1")]), "contentId-123456").convert("source")

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
