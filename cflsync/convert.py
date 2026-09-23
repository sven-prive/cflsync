# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Document markup conversion boundary."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Mapping

from .errors import SyncError

PANDOC_API_VERSION = (1, 23, 1, 2)
IMAGE_SUFFIXES = (".apng", ".avif", ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".tif", ".tiff", ".webp")


class PandocError(SyncError):
    """Raised when Pandoc cannot perform a required conversion."""


class ConversionError(SyncError):
    """Raised when document markup cannot be converted safely."""


class ADFToMarkdownConverter:
    """Convert supported ADF content to GFM, retaining unsupported structures."""

    def __init__(self, pandoc, media=None) -> None:
        self._pandoc_runner = pandoc
        self._media = media

    def convert(self, document: Mapping[str, object], title: str | None = None) -> str:
        """Convert an ADF body to GFM, optionally prefixed by its page title."""
        return self._pandoc_runner.pandoc_to_gfm(self._to_pandoc(document, title))

    def _to_pandoc(self, document, title=None):
        if document.get("type") != "doc" or document.get("version") != 1:
            raise ConversionError("ADF document must have type 'doc' and version 1")

        content = document.get("content")
        if not isinstance(content, list):
            raise ConversionError("ADF document content must be a list")

        blocks = self._convert_blocks(content)
        if title is not None:
            inlines = self._convert_text({"type": "text", "text": title})
            if not title or inlines is None:
                raise ConversionError("page title must be non-empty single-line text")

            blocks.insert(0, {"t": "Header", "c": [1, ["", [], []], inlines]})

        return {"pandoc-api-version": list(PANDOC_API_VERSION), "meta": {}, "blocks": blocks}

    def _convert_blocks(self, nodes):
        blocks = []
        for node in nodes:
            if not isinstance(node, Mapping):
                raise ConversionError("ADF block must be an object")

            blocks.append(self._convert_block(node))

        return blocks

    def _convert_block(self, node):
        node_type = node.get("type")
        if node_type == "paragraph":
            return self._convert_paragraph(node)

        if node_type == "heading":
            return self._convert_heading(node)

        if node_type == "blockquote":
            return self._convert_blockquote(node)

        if node_type == "bulletList":
            return self._convert_bullet_list(node)

        if node_type == "orderedList":
            return self._convert_ordered_list(node)

        if node_type == "codeBlock":
            return self._convert_code_block(node)

        if node_type == "rule":
            return self._convert_rule(node)

        if node_type == "table":
            return self._convert_table(node)

        if node_type == "mediaSingle":
            return self._convert_media_single(node)

        if node_type == "mediaGroup":
            return self._convert_media_group(node)

        return self._convert_opaque(node)

    def _convert_paragraph(self, node):
        inlines = self._convert_inlines(node)
        if inlines is None:
            return self._convert_opaque(node)

        return {"t": "Para", "c": inlines}

    def _convert_heading(self, node):
        attrs = node.get("attrs")
        if not isinstance(attrs, Mapping):
            return self._convert_opaque(node)

        level = attrs.get("level")
        inlines = self._convert_inlines(node)
        if type(level) is not int or not 1 <= level <= 6 or inlines is None:
            return self._convert_opaque(node)

        return {"t": "Header", "c": [max(2, level), ["", [], []], inlines]}

    def _convert_blockquote(self, node):
        content = self._convert_block_content(node)
        if content is None:
            return self._convert_opaque(node)

        return {"t": "BlockQuote", "c": self._convert_blocks(content)}

    def _convert_bullet_list(self, node):
        items = self._convert_list_items(node)
        if items is None:
            return self._convert_opaque(node)

        return {"t": "BulletList", "c": items}

    def _convert_ordered_list(self, node):
        attrs = node.get("attrs", {})
        if not isinstance(attrs, Mapping):
            return self._convert_opaque(node)

        order = attrs.get("order", 1)
        items = self._convert_list_items(node)
        if type(order) is not int or order < 1 or items is None:
            return self._convert_opaque(node)

        return {"t": "OrderedList", "c": [[order, {"t": "Decimal"}, {"t": "Period"}], items]}

    def _convert_code_block(self, node):
        attrs = node.get("attrs", {})
        if not isinstance(attrs, Mapping):
            return self._convert_opaque(node)

        language = attrs.get("language", "")
        content = node.get("content")
        if not isinstance(language, str) or not isinstance(content, list):
            return self._convert_opaque(node)

        text = []
        for child in content:
            if not isinstance(child, Mapping):
                return self._convert_opaque(node)

            if child.get("type") != "text" or not isinstance(child.get("text"), str):
                return self._convert_opaque(node)

            text.append(child["text"])

        return {"t": "CodeBlock", "c": [["", [language] if language else [], []], "".join(text)]}

    def _convert_rule(self, node):
        return {"t": "HorizontalRule"}

    def _convert_table(self, node):
        content = self._convert_block_content(node)
        if not content:
            return self._convert_opaque(node)

        rows = []
        columns = 0
        for child in content:
            if not isinstance(child, Mapping) or child.get("type") != "tableRow":
                return self._convert_opaque(node)

            cells = self._convert_table_cells(child)
            if cells is None:
                return self._convert_opaque(node)

            columns = max(columns, self._table_row_columns(child))
            rows.append([["", [], []], cells])

        head = rows[:1] if self._is_header_row(content[0]) else []
        colspecs = [[{"t": "AlignDefault"}, {"t": "ColWidthDefault"}] for _ in range(columns)]
        body = [["", [], []], 0, [], rows[len(head):]]

        return {"t": "Table", "c": [["", [], []], [None, []], colspecs, [["", [], []], head], [body], [["", [], []], []]]}

    def _convert_table_cells(self, row):
        content = self._convert_block_content(row)
        if not content:
            return None

        cells = []
        for child in content:
            cell = self._convert_table_cell(child)
            if cell is None:
                return None

            cells.append(cell)

        return cells

    def _convert_table_cell(self, node):
        if not isinstance(node, Mapping) or node.get("type") not in {"tableCell", "tableHeader"}:
            return None

        attrs = node.get("attrs", {})
        content = node.get("content", [])
        if not isinstance(attrs, Mapping) or not isinstance(content, list):
            return None

        rowspan = attrs.get("rowspan", 1)
        colspan = attrs.get("colspan", 1)
        if type(rowspan) is not int or type(colspan) is not int or rowspan < 1 or colspan < 1:
            return None

        return [["", [], []], {"t": "AlignDefault"}, rowspan, colspan, self._convert_blocks(content)]

    def _table_row_columns(self, row):
        columns = 0
        for cell in self._convert_block_content(row):
            columns += cell.get("attrs", {}).get("colspan", 1)

        return columns

    def _is_header_row(self, row):
        content = self._convert_block_content(row)
        if not content:
            return False

        return all(isinstance(cell, Mapping) and cell.get("type") == "tableHeader" for cell in content)

    def _convert_media_single(self, node):
        content = self._convert_block_content(node)
        if content is None or len(content) != 1:
            return self._convert_opaque(node)

        inline = self._convert_media(content[0])
        if inline is None:
            return self._convert_opaque(node)

        return {"t": "Para", "c": [inline]}

    def _convert_media_group(self, node):
        content = self._convert_block_content(node)
        if not content:
            return self._convert_opaque(node)

        media = []
        for child in content:
            inline = self._convert_media(child)
            if inline is None:
                return self._convert_opaque(node)

            media.append(inline)

        inlines = media[:1]
        for inline in media[1:]:
            inlines.append({"t": "Space"})
            inlines.append(inline)

        return {"t": "Para", "c": inlines}

    def _convert_media(self, node):
        if not isinstance(node, Mapping) or node.get("type") != "media":
            return None

        attrs = node.get("attrs")
        if not isinstance(attrs, Mapping):
            return None

        if attrs.get("type") == "external":
            url = attrs.get("url")
            if not isinstance(url, str) or not url:
                return None

            return self._convert_media_target(url, attrs.get("alt"))

        path = self._media_path(attrs)
        if path is None:
            return None

        return self._convert_media_target(path, attrs.get("alt"))

    def _media_path(self, attrs):
        file_id = attrs.get("id")
        if self._media is None or not isinstance(file_id, str) or not file_id:
            return None

        try:
            return self._media.path_for(file_id)
        except SyncError:
            return None

    def _convert_media_target(self, url, alt):
        text = alt if isinstance(alt, str) and alt else url.rsplit("/", 1)[-1]
        inlines = self._convert_text({"type": "text", "text": text})
        if inlines is None:
            return None

        node_type = "Image" if _is_image_target(url) else "Link"

        return {"t": node_type, "c": [["", [], []], inlines, [url, ""]]}

    def _convert_list_items(self, node):
        content = node.get("content")
        if not isinstance(content, list):
            return None

        items = []
        for item in content:
            if not isinstance(item, Mapping) or item.get("type") != "listItem":
                return None

            item_content = self._convert_block_content(item)
            if item_content is None:
                return None

            blocks = self._convert_blocks(item_content)
            if blocks and blocks[0].get("t") == "Para":
                blocks[0] = {"t": "Plain", "c": blocks[0]["c"]}

            items.append(blocks)

        return items

    def _convert_inlines(self, node):
        content = node.get("content", [])
        if not isinstance(content, list):
            return None

        inlines = []
        for child in content:
            if not isinstance(child, Mapping):
                return None

            converted = self._convert_inline(child)
            if converted is None:
                return None

            inlines.extend(converted)

        return inlines

    def _convert_inline(self, node):
        node_type = node.get("type")
        if node_type == "hardBreak":
            return self._convert_hard_break(node)

        if node_type == "text":
            return self._convert_text(node)

        return None

    def _convert_hard_break(self, node):
        return [{"t": "LineBreak"}]

    def _convert_text(self, node):
        text = node.get("text")
        if not isinstance(text, str) or "\n" in text or "\r" in text or "\t" in text:
            return None

        marks = node.get("marks", [])
        if not isinstance(marks, list):
            return None

        inlines = []
        for index, part in enumerate(text.split(" ")):
            if index:
                inlines.append({"t": "Space"})

            if part:
                inlines.append({"t": "Str", "c": part})

        return self._convert_marks(inlines, marks)

    def _convert_marks(self, inlines, marks):
        values = {}
        for mark in marks:
            if not isinstance(mark, Mapping) or not isinstance(mark.get("type"), str):
                return None

            mark_type = mark["type"]
            if mark_type not in {"strong", "em", "strike", "code", "link"}:
                continue

            if mark_type in values:
                return None

            values[mark_type] = mark

        result = inlines
        if "code" in values:
            result = self._convert_code_mark(inlines)
            if result is None:
                return None

        for mark_type, pandoc_type in (("strike", "Strikeout"), ("em", "Emph"), ("strong", "Strong")):
            if mark_type in values:
                result = [{"t": pandoc_type, "c": result}]

        if "link" in values:
            return self._convert_link_mark(result, values["link"])

        return result

    def _convert_code_mark(self, inlines):
        text = []
        for inline in inlines:
            if inline.get("t") == "Space":
                text.append(" ")
            elif inline.get("t") == "Str" and isinstance(inline.get("c"), str):
                text.append(inline["c"])
            else:
                return None

        return [{"t": "Code", "c": [["", [], []], "".join(text)]}]

    def _convert_link_mark(self, inlines, mark):
        attrs = mark.get("attrs")
        if not isinstance(attrs, Mapping):
            return None

        href = attrs.get("href")
        title = attrs.get("title", "")
        if not isinstance(href, str) or not href or not isinstance(title, str):
            return None

        return [{"t": "Link", "c": [["", [], []], inlines, [href, title]]}]

    def _convert_block_content(self, node):
        content = node.get("content")
        if not isinstance(content, list):
            return None

        return content

    def _convert_opaque(self, node):
        try:
            payload = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ConversionError("unsupported ADF node cannot be represented as JSON") from error

        return {"t": "CodeBlock", "c": [["", ["atlas_doc_format"], []], payload]}


class MarkdownToADFConverter:
    """Convert the supported GFM subset to ADF."""

    def __init__(self, pandoc, media=None, collection: str | None = None) -> None:
        self._pandoc_runner = pandoc
        self._media = media
        self._collection = collection

    def convert(self, markdown: str) -> Mapping[str, object]:
        """Convert one GFM document to ADF."""
        return self._to_adf(self._pandoc_runner.gfm_to_pandoc(markdown))

    def _to_adf(self, pandoc):
        if not isinstance(pandoc, Mapping):
            raise ConversionError("Pandoc document must be an object")

        if set(pandoc) != {"pandoc-api-version", "meta", "blocks"}:
            raise ConversionError("Pandoc document has unsupported fields")

        version = pandoc.get("pandoc-api-version")
        if version != list(PANDOC_API_VERSION):
            raise ConversionError("Pandoc document has an unsupported API version")

        if pandoc.get("meta") != {}:
            raise ConversionError("Pandoc document metadata cannot be represented in ADF")

        blocks = pandoc.get("blocks")
        if not isinstance(blocks, list):
            raise ConversionError("Pandoc document blocks must be a list")

        return {"type": "doc", "version": 1, "content": self._convert_blocks(blocks)}

    def _convert_blocks(self, pandoc_blocks):
        blocks = []
        for pandoc_block in pandoc_blocks:
            if not isinstance(pandoc_block, Mapping):
                raise ConversionError("Pandoc block must be an object")

            blocks.append(self._convert_block(pandoc_block))

        return blocks

    def _convert_block(self, pandoc_block):
        node_type = pandoc_block.get("t")
        if node_type == "Para":
            return self._convert_paragraph(pandoc_block)

        if node_type == "Plain":
            return self._convert_plain(pandoc_block)

        if node_type == "Header":
            return self._convert_heading(pandoc_block)

        if node_type == "BlockQuote":
            return self._convert_blockquote(pandoc_block)

        if node_type == "BulletList":
            return self._convert_bullet_list(pandoc_block)

        if node_type == "OrderedList":
            return self._convert_ordered_list(pandoc_block)

        if node_type == "CodeBlock":
            return self._convert_code_block(pandoc_block)

        if node_type == "HorizontalRule":
            return self._convert_rule(pandoc_block)

        if node_type == "Table":
            return self._convert_table(pandoc_block)

        if node_type == "RawBlock":
            return self._convert_raw_block(pandoc_block)

        raise ConversionError(f"unsupported Pandoc block '{node_type}'")

    def _convert_paragraph(self, pandoc_block):
        media = self._convert_media_block(pandoc_block)
        if media is not None:
            return media

        return {"type": "paragraph", "content": self._convert_inlines(pandoc_block)}

    def _convert_plain(self, pandoc_block):
        media = self._convert_media_block(pandoc_block)
        if media is not None:
            return media

        return {"type": "paragraph", "content": self._convert_inlines(pandoc_block)}

    def _convert_heading(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc heading has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list) or len(value) != 3:
            raise ConversionError("Pandoc heading has invalid content")

        level, attributes, inlines = value
        if type(level) is not int or not 1 <= level <= 6 or attributes != ["", [], []] or not isinstance(inlines, list):
            raise ConversionError("Pandoc heading has unsupported attributes")

        return {"type": "heading", "attrs": {"level": level}, "content": self._convert_inline_nodes(inlines)}

    def _convert_blockquote(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc block quote has unsupported fields")

        blocks = pandoc_block.get("c")
        if not isinstance(blocks, list):
            raise ConversionError("Pandoc block quote content must be a list")

        return {"type": "blockquote", "content": self._convert_blocks(blocks)}

    def _convert_bullet_list(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc bullet list has unsupported fields")

        items = pandoc_block.get("c")
        if not isinstance(items, list):
            raise ConversionError("Pandoc bullet list content must be a list")

        return {"type": "bulletList", "content": self._convert_list_items(items)}

    def _convert_ordered_list(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc ordered list has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list) or len(value) != 2:
            raise ConversionError("Pandoc ordered list has invalid content")

        attributes, items = value
        if not isinstance(attributes, list) or len(attributes) != 3 or attributes[1:] != [{"t": "Decimal"}, {"t": "Period"}]:
            raise ConversionError("Pandoc ordered list has unsupported attributes")

        order = attributes[0]
        if type(order) is not int or order < 1 or not isinstance(items, list):
            raise ConversionError("Pandoc ordered list has invalid content")

        return {"type": "orderedList", "attrs": {"order": order}, "content": self._convert_list_items(items)}

    def _convert_code_block(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc code block has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list) or len(value) != 2:
            raise ConversionError("Pandoc code block has invalid content")

        attributes, text = value
        if attributes == ["", ["atlas_doc_format"], []]:
            return self._convert_opaque_block(text)

        if not isinstance(attributes, list) or len(attributes) != 3:
            raise ConversionError("Pandoc code block has unsupported attributes")

        identifier, classes, key_values = attributes
        if identifier != "" or not isinstance(classes, list) or len(classes) > 1 or key_values != []:
            raise ConversionError("Pandoc code block has unsupported attributes")

        if not all(isinstance(value, str) for value in classes) or "atlas_doc_format" in classes or not isinstance(text, str):
            raise ConversionError("Pandoc code block has unsupported content")

        if not classes:
            return {"type": "codeBlock", "content": [{"type": "text", "text": text}]}

        return {"type": "codeBlock", "attrs": {"language": classes[0]}, "content": [{"type": "text", "text": text}]}

    def _convert_rule(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t"}):
            raise ConversionError("Pandoc horizontal rule has unsupported fields")

        return {"type": "rule"}

    def _convert_table(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc table has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list) or len(value) != 6:
            raise ConversionError("Pandoc table has invalid content")

        attributes, caption, colspecs, head, bodies, foot = value
        if attributes != ["", [], []]:
            raise ConversionError("Pandoc table has unsupported attributes")

        if not isinstance(caption, list) or len(caption) != 2 or caption[1] != []:
            raise ConversionError("Pandoc table captions cannot be represented in ADF")

        if not isinstance(foot, list) or len(foot) != 2 or foot[1] != []:
            raise ConversionError("Pandoc table footers cannot be represented in ADF")

        rows = self._convert_table_head(head) + self._convert_table_bodies(bodies)
        if not rows:
            raise ConversionError("Pandoc table has no rows")

        return {"type": "table", "content": rows}

    def _convert_table_head(self, head):
        if not isinstance(head, list) or len(head) != 2 or not isinstance(head[1], list):
            raise ConversionError("Pandoc table head has invalid content")

        return [self._convert_table_row(row, "tableHeader") for row in head[1]]

    def _convert_table_bodies(self, bodies):
        if not isinstance(bodies, list):
            raise ConversionError("Pandoc table bodies must be a list")

        rows = []
        for body in bodies:
            if not isinstance(body, list) or len(body) != 4:
                raise ConversionError("Pandoc table body has invalid content")

            attributes, head_columns, head_rows, body_rows = body
            if head_columns != 0 or head_rows != []:
                raise ConversionError("Pandoc table row headers cannot be represented in ADF")

            if not isinstance(body_rows, list):
                raise ConversionError("Pandoc table body rows must be a list")

            for row in body_rows:
                rows.append(self._convert_table_row(row, "tableCell"))

        return rows

    def _convert_table_row(self, row, cell_type):
        if not isinstance(row, list) or len(row) != 2 or not isinstance(row[1], list):
            raise ConversionError("Pandoc table row has invalid content")

        return {"type": "tableRow", "content": [self._convert_table_cell(cell, cell_type) for cell in row[1]]}

    def _convert_table_cell(self, cell, cell_type):
        if not isinstance(cell, list) or len(cell) != 5:
            raise ConversionError("Pandoc table cell has invalid content")

        attributes, alignment, rowspan, colspan, blocks = cell
        if attributes != ["", [], []] or not isinstance(blocks, list):
            raise ConversionError("Pandoc table cell has unsupported attributes")

        if type(rowspan) is not int or type(colspan) is not int or rowspan < 1 or colspan < 1:
            raise ConversionError("Pandoc table cell has invalid spans")

        content = self._convert_blocks(blocks)
        if not content:
            content = [{"type": "paragraph"}]

        return {"type": cell_type, "attrs": {"colspan": colspan, "rowspan": rowspan}, "content": content}

    def _convert_raw_block(self, pandoc_block):
        """Recover an HTML table, which is how GFM represents a table Pandoc cannot pipe."""
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc raw block has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list) or len(value) != 2 or value[0] != "html" or not isinstance(value[1], str):
            raise ConversionError("raw content other than an HTML table cannot be represented in ADF")

        if not value[1].lstrip().startswith("<table"):
            raise ConversionError("raw content other than an HTML table cannot be represented in ADF")

        blocks = self._pandoc_runner.html_to_pandoc(value[1]).get("blocks")
        if not isinstance(blocks, list) or len(blocks) != 1 or not isinstance(blocks[0], Mapping):
            raise ConversionError("raw HTML must contain exactly one table")

        return self._convert_table(blocks[0])

    def _convert_media_block(self, pandoc_block):
        value = pandoc_block.get("c")
        if not isinstance(value, list):
            return None

        images = [inline for inline in value if isinstance(inline, Mapping) and inline.get("t") == "Image"]
        if not images:
            return None

        if any(not isinstance(inline, Mapping) or inline.get("t") not in {"Image", "Space"} for inline in value):
            raise ConversionError("Pandoc image must be the only content of its paragraph")

        content = [self._convert_image(image) for image in images]
        if len(content) == 1:
            return {"type": "mediaSingle", "attrs": {"layout": "center"}, "content": content}

        return {"type": "mediaGroup", "content": content}

    def _convert_image(self, pandoc_inline):
        if not self._has_fields(pandoc_inline, {"t", "c"}):
            raise ConversionError("Pandoc image has unsupported fields")

        value = pandoc_inline.get("c")
        if not isinstance(value, list) or len(value) != 3:
            raise ConversionError("Pandoc image has invalid content")

        attributes, description, target = value
        if attributes != ["", [], []] or not isinstance(description, list) or not isinstance(target, list) or len(target) != 2:
            raise ConversionError("Pandoc image has unsupported attributes")

        url, title = target
        if not isinstance(url, str) or not url or not isinstance(title, str):
            raise ConversionError("Pandoc image has invalid target")

        if url.startswith("_attachments/"):
            attrs = {"type": "file", "id": self._media_id(url), "collection": self._media_collection()}
        else:
            attrs = {"type": "external", "url": url}

        alt = self._image_alt(description)
        if alt:
            attrs["alt"] = alt

        return {"type": "media", "attrs": attrs}

    def _media_id(self, url):
        if self._media is None:
            raise ConversionError(f"attachment path '{url}' needs an attachment manifest")

        return self._media.id_for(url)

    def _media_collection(self):
        if self._collection is None:
            raise ConversionError("attachment references need a media collection")

        return self._collection

    def _image_alt(self, description):
        text = []
        for inline in description:
            if not isinstance(inline, Mapping):
                raise ConversionError("Pandoc image description must contain inlines")

            if inline.get("t") == "Space":
                text.append(" ")
            elif inline.get("t") == "Str" and isinstance(inline.get("c"), str):
                text.append(inline["c"])
            else:
                raise ConversionError("Pandoc image description must be plain text")

        return "".join(text)

    def _convert_list_items(self, items):
        result = []
        for item in items:
            if not isinstance(item, list) or not item:
                raise ConversionError("Pandoc list item must contain blocks")

            result.append({"type": "listItem", "content": self._convert_blocks(item)})

        return result

    def _convert_inlines(self, pandoc_block):
        if not self._has_fields(pandoc_block, {"t", "c"}):
            raise ConversionError("Pandoc paragraph has unsupported fields")

        value = pandoc_block.get("c")
        if not isinstance(value, list):
            raise ConversionError("Pandoc paragraph content must be a list")

        return self._convert_inline_nodes(value)

    def _convert_inline_nodes(self, pandoc_inlines):
        inlines = []
        self._convert_inline_nodes_into(pandoc_inlines, inlines, [])

        return inlines

    def _convert_inline_nodes_into(self, pandoc_inlines, inlines, marks):
        for pandoc_inline in pandoc_inlines:
            if not isinstance(pandoc_inline, Mapping):
                raise ConversionError("Pandoc inline must be an object")

            self._convert_inline(pandoc_inline, inlines, marks)

    def _convert_inline(self, pandoc_inline, inlines, marks):
        node_type = pandoc_inline.get("t")
        if node_type == "Str":
            self._convert_text(pandoc_inline, inlines, marks)
            return

        if node_type == "Space":
            self._convert_space(pandoc_inline, inlines, marks)
            return

        if node_type == "LineBreak":
            self._convert_hard_break(pandoc_inline, inlines, marks)
            return

        if node_type == "Strong":
            self._convert_strong(pandoc_inline, inlines, marks)
            return

        if node_type == "Emph":
            self._convert_emphasis(pandoc_inline, inlines, marks)
            return

        if node_type == "Strikeout":
            self._convert_strikeout(pandoc_inline, inlines, marks)
            return

        if node_type == "Code":
            self._convert_code(pandoc_inline, inlines, marks)
            return

        if node_type == "Link":
            self._convert_link(pandoc_inline, inlines, marks)
            return

        raise ConversionError(f"unsupported Pandoc inline '{node_type}'")

    def _convert_text(self, pandoc_inline, inlines, marks):
        if not self._has_fields(pandoc_inline, {"t", "c"}) or not isinstance(pandoc_inline.get("c"), str):
            raise ConversionError("Pandoc text has invalid content")

        self._append_text(inlines, pandoc_inline["c"], marks)

    def _convert_space(self, pandoc_inline, inlines, marks):
        if not self._has_fields(pandoc_inline, {"t"}):
            raise ConversionError("Pandoc space has unsupported fields")

        self._append_text(inlines, " ", marks)

    def _convert_hard_break(self, pandoc_inline, inlines, marks):
        if not self._has_fields(pandoc_inline, {"t"}) or marks:
            raise ConversionError("Pandoc line break has unsupported marks or fields")

        inlines.append({"type": "hardBreak"})

    def _convert_strong(self, pandoc_inline, inlines, marks):
        self._convert_marked_inlines(pandoc_inline, inlines, marks, {"type": "strong"})

    def _convert_emphasis(self, pandoc_inline, inlines, marks):
        self._convert_marked_inlines(pandoc_inline, inlines, marks, {"type": "em"})

    def _convert_strikeout(self, pandoc_inline, inlines, marks):
        self._convert_marked_inlines(pandoc_inline, inlines, marks, {"type": "strike"})

    def _convert_code(self, pandoc_inline, inlines, marks):
        if not self._has_fields(pandoc_inline, {"t", "c"}):
            raise ConversionError("Pandoc code has unsupported fields")

        value = pandoc_inline.get("c")
        if not isinstance(value, list) or len(value) != 2 or value[0] != ["", [], []] or not isinstance(value[1], str):
            raise ConversionError("Pandoc code has invalid content")

        if marks or not value[1]:
            raise ConversionError("Pandoc code has unsupported marks or content")

        self._append_text(inlines, value[1], [{"type": "code"}])

    def _convert_link(self, pandoc_inline, inlines, marks):
        if not self._has_fields(pandoc_inline, {"t", "c"}):
            raise ConversionError("Pandoc link has unsupported fields")

        value = pandoc_inline.get("c")
        if not isinstance(value, list) or len(value) != 3:
            raise ConversionError("Pandoc link has invalid content")

        attributes, content, target = value
        if attributes != ["", [], []] or not isinstance(content, list) or not isinstance(target, list) or len(target) != 2:
            raise ConversionError("Pandoc link has unsupported attributes")

        href, title = target
        if not isinstance(href, str) or not href or not isinstance(title, str):
            raise ConversionError("Pandoc link has invalid target")

        mark = {"type": "link", "attrs": {"href": href, "title": title}}
        self._convert_marked_content(content, inlines, marks, mark)

    def _convert_marked_inlines(self, pandoc_inline, inlines, marks, mark):
        value = pandoc_inline.get("c")
        if not isinstance(value, list):
            raise ConversionError("Pandoc mark content must be a list")

        self._convert_marked_content(value, inlines, marks, mark)

    def _convert_marked_content(self, value, inlines, marks, mark):
        mark_type = mark["type"]
        if any(existing["type"] == mark_type for existing in marks):
            raise ConversionError(f"Pandoc inline has duplicate '{mark_type}' marks")

        before = len(inlines)
        nested_marks = list(marks)
        nested_marks.append(mark)
        self._convert_inline_nodes_into(value, inlines, nested_marks)
        if len(inlines) == before:
            raise ConversionError("Pandoc mark content must not be empty")

    def _append_text(self, inlines, text, marks):
        if not text or "\n" in text or "\r" in text or "\t" in text:
            raise ConversionError("Pandoc text has unsupported whitespace")

        if inlines and inlines[-1].get("type") == "text" and inlines[-1].get("marks", []) == marks:
            inlines[-1]["text"] += text
            return

        node = {"type": "text", "text": text}
        if marks:
            node["marks"] = marks

        inlines.append(node)

    def _convert_opaque_block(self, text):
        if not isinstance(text, str):
            raise ConversionError("opaque ADF marker must contain JSON text")

        try:
            node = json.loads(text)
        except json.JSONDecodeError as error:
            raise ConversionError("opaque ADF marker contains invalid JSON") from error

        if not isinstance(node, dict) or not isinstance(node.get("type"), str):
            raise ConversionError("opaque ADF marker must contain an ADF node")

        inline_types = {"doc", "text", "hardBreak", "status", "mention", "date", "emoji", "inlineCard", "mediaInline"}
        if node["type"] in inline_types:
            raise ConversionError("opaque ADF node is invalid in a block context")

        return node

    def _has_fields(self, node, required):
        return set(node) == required


class PandocRunner:
    """Run a compatible Pandoc executable with GFM and native JSON."""

    API_VERSION = PANDOC_API_VERSION

    def __init__(self, binary: str = "pandoc", run=None) -> None:
        self.binary = binary
        self._run_process = run or subprocess.run
        self.version = self._discover_version()
        self._validate_api_version()

    def gfm_to_pandoc(self, gfm: str) -> dict[str, object]:
        """Parse GFM to a validated Pandoc native JSON document."""
        return self._pandoc(self._run(["--from=gfm", "--to=json"], gfm))

    def html_to_pandoc(self, html: str) -> dict[str, object]:
        """Parse an HTML fragment to a validated Pandoc native JSON document."""
        return self._pandoc(self._run(["--from=html", "--to=json"], html))

    def pandoc_to_gfm(self, pandoc: Mapping[str, object]) -> str:
        """Render a validated Pandoc native JSON document to canonical GFM."""
        self._validate_pandoc(pandoc)
        return self._run(["--from=json", "--to=gfm", "--wrap=none"], json.dumps(pandoc, ensure_ascii=False, separators=(",", ":")))

    def _discover_version(self) -> str:
        output = self._run(["--version"], "")
        match = re.match(r"pandoc (\S+)", output)
        if match is None:
            raise PandocError(f"cannot determine Pandoc version from '{self.binary}'")

        return match.group(1)

    def _validate_api_version(self) -> None:
        pandoc = self._pandoc(self._run(["--from=gfm", "--to=json"], ""))
        self._validate_pandoc(pandoc)

    def _pandoc(self, text: str) -> dict[str, object]:
        try:
            pandoc = json.loads(text)
        except json.JSONDecodeError as error:
            raise PandocError("Pandoc did not produce valid native JSON") from error
        if not isinstance(pandoc, dict):
            raise PandocError("Pandoc native JSON must be an object")

        self._validate_pandoc(pandoc)

        return pandoc

    def _validate_pandoc(self, pandoc: Mapping[str, object]) -> None:
        version = pandoc.get("pandoc-api-version")
        if not isinstance(version, list) or any(type(part) is not int for part in version):
            raise PandocError("Pandoc native JSON has no valid pandoc-api-version")
        if tuple(version) != self.API_VERSION:
            expected = ".".join(map(str, self.API_VERSION))
            actual = ".".join(map(str, version))
            raise PandocError(f"Pandoc JSON API version {actual} is unsupported; expected {expected}")

        if not isinstance(pandoc.get("meta"), Mapping):
            raise PandocError("Pandoc native JSON has no meta object")

        if not isinstance(pandoc.get("blocks"), list):
            raise PandocError("Pandoc native JSON has no blocks list")

    def _run(self, arguments: list[str], input_text: str) -> str:
        command = [self.binary, *arguments]
        try:
            result = self._run_process(command, input=input_text, text=True, capture_output=True)
        except FileNotFoundError as error:
            raise PandocError(f"Pandoc executable '{self.binary}' was not found") from error
        except OSError as error:
            raise PandocError(f"cannot run Pandoc executable '{self.binary}': {error}") from error
        if result.returncode != 0:
            detail = result.stderr.strip() or f"exit status {result.returncode}"
            raise PandocError(f"Pandoc failed: {detail}")

        if not isinstance(result.stdout, str):
            raise PandocError("Pandoc produced non-text output")

        return result.stdout


def _is_image_target(url):
    """Report whether *url* names a file GFM can render as an image."""
    path = url.split("?", 1)[0].split("#", 1)[0].lower()

    return path.endswith(IMAGE_SUFFIXES)


# vim: set ts=4 sw=4 et tw=132:
