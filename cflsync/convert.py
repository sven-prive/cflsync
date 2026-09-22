# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Document markup conversion boundary."""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Iterable, Mapping

from .errors import SyncError

PANDOC_API_VERSION = (1, 23, 1, 2)


class PandocError(SyncError):
    """Raised when Pandoc cannot perform a required conversion."""


class ConversionError(SyncError):
    """Raised when document markup cannot be converted without loss."""


class MediaResolutionError(SyncError):
    """Raised when a managed attachment cannot be resolved safely."""


class MediaResolver:
    """Map a page attachment manifest between IDs and local paths."""

    def __init__(self, manifest: Iterable[tuple[str, str]]) -> None:
        paths_by_id = {}
        ids_by_filename = {}
        for filename, attachment_id in manifest:
            self._validate_filename(filename)
            self._validate_attachment_id(attachment_id)
            if filename in ids_by_filename:
                raise MediaResolutionError(f"attachment filename '{filename}' is ambiguous")

            if attachment_id in paths_by_id:
                raise MediaResolutionError(f"attachment ID '{attachment_id}' is ambiguous")

            paths_by_id[attachment_id] = f"_attachments/{filename}"
            ids_by_filename[filename] = attachment_id

        self._paths_by_id = paths_by_id
        self._ids_by_filename = ids_by_filename

    def path_for(self, attachment_id: str) -> str:
        """Return the managed Markdown path for one attachment ID."""
        self._validate_attachment_id(attachment_id)
        try:
            return self._paths_by_id[attachment_id]
        except KeyError as error:
            raise MediaResolutionError(f"attachment ID '{attachment_id}' is not managed") from error

    def id_for(self, path: str) -> str:
        """Return the attachment ID for one managed Markdown path."""
        filename = self._filename_from_path(path)
        try:
            return self._ids_by_filename[filename]
        except KeyError as error:
            raise MediaResolutionError(f"attachment path '{path}' is not managed") from error

    def _validate_filename(self, filename):
        if not isinstance(filename, str) or not filename or filename in {".", ".."}:
            raise MediaResolutionError("attachment filename must be a non-empty basename")

        if "/" in filename or "\\" in filename or "\x00" in filename:
            raise MediaResolutionError(f"attachment filename '{filename}' is unsafe")

    def _validate_attachment_id(self, attachment_id):
        if not isinstance(attachment_id, str) or not attachment_id:
            raise MediaResolutionError("attachment ID must be a non-empty string")

    def _filename_from_path(self, path):
        if not isinstance(path, str):
            raise MediaResolutionError("attachment path must be a string")

        parts = path.split("/")
        if len(parts) != 2 or parts[0] != "_attachments":
            raise MediaResolutionError(f"attachment path '{path}' is outside _attachments")

        filename = parts[1]
        self._validate_filename(filename)

        return filename


class ADFToMarkdownConverter:
    """Convert the supported ADF subset to canonical GFM."""

    def __init__(self, pandoc) -> None:
        self._pandoc_runner = pandoc

    def convert(self, document: Mapping[str, object]) -> str:
        """Convert one ADF document to GFM."""
        return self._pandoc_runner.pandoc_to_gfm(self._to_pandoc(document))

    def _to_pandoc(self, document):
        if document.get("type") != "doc" or document.get("version") != 1:
            raise ConversionError("ADF document must have type 'doc' and version 1")

        if set(document) != {"type", "version", "content"}:
            raise ConversionError("ADF document has unsupported fields")

        content = document.get("content")
        if not isinstance(content, list):
            raise ConversionError("ADF document content must be a list")

        return {"pandoc-api-version": list(PANDOC_API_VERSION), "meta": {}, "blocks": self._convert_blocks(content)}

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

        return self._convert_opaque(node)

    def _convert_paragraph(self, node):
        inlines = self._convert_inlines(node)
        if inlines is None:
            return self._convert_opaque(node)

        return {"t": "Para", "c": inlines}

    def _convert_heading(self, node):
        if not self._has_fields(node, {"type", "attrs", "content"}):
            return self._convert_opaque(node)

        attrs = node.get("attrs")
        if not isinstance(attrs, Mapping) or set(attrs) != {"level"}:
            return self._convert_opaque(node)

        level = attrs.get("level")
        inlines = self._convert_inlines(node, {"attrs"})
        if type(level) is not int or not 1 <= level <= 6 or inlines is None:
            return self._convert_opaque(node)

        return {"t": "Header", "c": [level, ["", [], []], inlines]}

    def _convert_blockquote(self, node):
        content = self._convert_block_content(node)
        if content is None:
            return self._convert_opaque(node)

        return {"t": "BlockQuote", "c": self._convert_blocks(content)}

    def _convert_bullet_list(self, node):
        if not self._has_fields(node, {"type", "content"}):
            return self._convert_opaque(node)

        items = self._convert_list_items(node)
        if items is None:
            return self._convert_opaque(node)

        return {"t": "BulletList", "c": items}

    def _convert_ordered_list(self, node):
        if not self._has_fields(node, {"type", "content"}, {"attrs"}):
            return self._convert_opaque(node)

        attrs = node.get("attrs", {})
        if not isinstance(attrs, Mapping) or set(attrs) - {"order"}:
            return self._convert_opaque(node)

        order = attrs.get("order", 1)
        items = self._convert_list_items(node)
        if type(order) is not int or order < 1 or items is None:
            return self._convert_opaque(node)

        return {"t": "OrderedList", "c": [[order, {"t": "Decimal"}, {"t": "Period"}], items]}

    def _convert_code_block(self, node):
        if not self._has_fields(node, {"type", "content"}, {"attrs"}):
            return self._convert_opaque(node)

        attrs = node.get("attrs", {})
        if not isinstance(attrs, Mapping) or set(attrs) - {"language"}:
            return self._convert_opaque(node)

        language = attrs.get("language", "")
        content = node.get("content")
        if not isinstance(language, str) or not isinstance(content, list):
            return self._convert_opaque(node)

        text = []
        for child in content:
            if not isinstance(child, Mapping) or not self._has_fields(child, {"type", "text"}):
                return self._convert_opaque(node)

            if child.get("type") != "text" or not isinstance(child.get("text"), str):
                return self._convert_opaque(node)

            text.append(child["text"])

        return {"t": "CodeBlock", "c": [["", [language] if language else [], []], "".join(text)]}

    def _convert_rule(self, node):
        if not self._has_fields(node, {"type"}):
            return self._convert_opaque(node)

        return {"t": "HorizontalRule"}

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
                items.append([self._convert_opaque(item)])
                continue

            blocks = self._convert_blocks(item_content)
            if blocks and blocks[0].get("t") == "Para":
                blocks[0] = {"t": "Plain", "c": blocks[0]["c"]}

            items.append(blocks)

        return items

    def _convert_inlines(self, node, optional=None):
        content = self._convert_inline_content(node, optional)
        if content is None:
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
        if not self._has_fields(node, {"type"}):
            return None

        return [{"t": "LineBreak"}]

    def _convert_text(self, node):
        if not self._has_fields(node, {"type", "text"}, {"marks"}):
            return None

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
            if mark_type in values or mark_type not in {"strong", "em", "strike", "code", "link"}:
                return None

            values[mark_type] = mark
        if "code" in values:
            return self._convert_code_mark(inlines, values)

        result = inlines
        for mark_type, pandoc_type in (("strike", "Strikeout"), ("em", "Emph"), ("strong", "Strong")):
            if mark_type in values:
                if not self._has_fields(values[mark_type], {"type"}):
                    return None

                result = [{"t": pandoc_type, "c": result}]
        if "link" in values:
            return self._convert_link_mark(result, values["link"])

        return result

    def _convert_code_mark(self, inlines, values):
        if len(values) != 1 or not self._has_fields(values["code"], {"type"}):
            return None

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
        if not self._has_fields(mark, {"type", "attrs"}):
            return None

        attrs = mark.get("attrs")
        if not isinstance(attrs, Mapping) or set(attrs) - {"href", "title"}:
            return None

        href = attrs.get("href")
        title = attrs.get("title", "")
        if not isinstance(href, str) or not href or not isinstance(title, str):
            return None

        return [{"t": "Link", "c": [["", [], []], inlines, [href, title]]}]

    def _convert_block_content(self, node):
        if not self._has_fields(node, {"type", "content"}):
            return None

        content = node.get("content")
        if not isinstance(content, list):
            return None

        return content

    def _convert_inline_content(self, node, optional=None):
        if not self._has_fields(node, {"type", "content"}, optional):
            return None

        content = node.get("content")
        if not isinstance(content, list):
            return None

        return content

    def _has_fields(self, node, required, optional=None):
        return required <= set(node) and set(node) <= required | (optional or set())

    def _convert_opaque(self, node):
        try:
            payload = json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as error:
            raise ConversionError("unsupported ADF node cannot be represented as JSON") from error

        return {"t": "CodeBlock", "c": [["", ["atlas_doc_format"], []], payload]}


class MarkdownToADFConverter:
    """Convert the supported GFM subset to ADF."""

    def __init__(self, pandoc) -> None:
        self._pandoc_runner = pandoc

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

        raise ConversionError(f"unsupported Pandoc block '{node_type}'")

    def _convert_paragraph(self, pandoc_block):
        return {"type": "paragraph", "content": self._convert_inlines(pandoc_block)}

    def _convert_plain(self, pandoc_block):
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


# vim: set ts=4 sw=4 et tw=132:
