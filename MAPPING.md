# Atlassian Document Format to Pandoc AST mapping

This document specifies the `cflsync` conversion boundary:

```text
atlas_doc_format ⇄ Pandoc AST ⇄ GFM
```

Pandoc supplies the second conversion through its `gfm` reader and writer.
`cflsync` exposes `ADFToMarkdownConverter` and `MarkdownToADFConverter`;
Pandoc's JSON AST is an internal representation. The ADF API payload is a
JSON-encoded ADF document; it is decoded before conversion and encoded again
when sent to Confluence.

ADF is the remote source of truth. The mapping is loss-aware: supported ADF is
represented by ordinary Pandoc elements; unsupported ADF is retained as raw
ADF JSON in a Pandoc code block, which Pandoc writes as a fenced GFM block.

## Conversion invariants

- Every emitted ADF document has `type: "doc"` and `version: 1`.
- Unsupported ADF data is never silently dropped or approximated.
- Ordinary Pandoc GFM is not interpreted as a Confluence-specific construct
  unless it matches a mapping below or a cflsync opaque marker.
- An opaque marker is decoded only as JSON and validated in its destination
  parent context; malformed, altered, or contextually invalid data fails
  before an API request.
- The resulting GFM is canonicalized by Pandoc. Preservation concerns document
  structure and opaque JSON payloads, not original Markdown spelling.

## Root document

| ADF | Pandoc AST |
| --- | --- |
| `{ "type": "doc", "version": 1, "content": [...] }` | `Pandoc Meta [Block]` |
| Empty ADF document | `Pandoc Meta []` |

ADF page metadata such as page ID, title, version, and parent is not body
content. It remains in the cflsync page cache rather than Pandoc metadata.

## Direct block mappings

| ADF node | Pandoc AST | Reverse ADF node |
| --- | --- | --- |
| `paragraph` | `Para` | `paragraph` |
| `heading` with `attrs.level` 1–6 | `Header level` | `heading` with `attrs.level` |
| `blockquote` | `BlockQuote` | `blockquote` |
| `bulletList` and `listItem` | `BulletList` | `bulletList` and `listItem` |
| `orderedList` and `listItem` | `OrderedList` | `orderedList` and `listItem` |
| `codeBlock` | `CodeBlock`; `attrs.language` becomes its language class | `codeBlock` with `attrs.language` |
| `rule` | `HorizontalRule` | `rule` |

ADF list attributes that Pandoc represents—principally ordered-list start
number—are retained. Unsupported list, heading, code-block, or layout
attributes cause the smallest enclosing ADF block to use opaque retention.

## Direct inline mappings

| ADF node or mark | Pandoc AST | Reverse ADF form |
| --- | --- | --- |
| `text` | `Str` and `Space` | `text` |
| `hardBreak` | `LineBreak` | `hardBreak` |
| `strong` mark | `Strong` | `strong` mark |
| `em` mark | `Emph` | `em` mark |
| `strike` mark | `Strikeout` | `strike` mark |
| `code` mark | `Code` | `code` mark |
| `link` mark | `Link` | `link` mark |

Text marks are emitted in a deterministic nesting order. If an ADF mark set
cannot be represented as a properly nested Pandoc inline tree, its enclosing
ADF block is retained opaquely.

## Tables and media

An ADF `table` maps to Pandoc `Table` only when it has no spans, no layout or
styling attributes that affect meaning, and cells can be represented by the
Pandoc/GFM table subset. The reverse mapping emits `table`, `tableRow`, and
`tableHeader` or `tableCell` nodes. All other tables are opaque.

`media`, `mediaInline`, `mediaSingle`, and `mediaGroup` map to Pandoc `Image`
or `Link` only when the page attachment manifest resolves the ADF media
identifier to a managed local `_attachments/<filename>` path. The reverse
mapping uses the manifest to reconstruct the corresponding ADF media node.
Unresolved media, remote media, and media attributes not represented by a
Pandoc image or link are opaque.

## Opaque ADF retention

### Marker representation

An opaque ADF node is represented in the Pandoc AST as:

```text
CodeBlock ("", ["atlas_doc_format"], []) JSON
```

`JSON` is a canonical JSON serialization of one complete ADF node. Pandoc's
GFM writer renders this as a fenced code block with `atlas_doc_format` as its info
string:

````markdown
```atlas_doc_format
{"type":"expand","attrs":{"title":"Details"},"content":[...]}
```
````

The ADF conversion recognizes only this exact marker class. It parses the JSON
and inserts the node into the current ADF container. It must not turn the fence
into an ADF `codeBlock`.

### Retention granularity

ADF has inline nodes such as `status`, `mention`, `date`, `emoji`,
`inlineCard`, and `mediaInline`. A GFM fence is a block construct and cannot
occupy a position inside a Pandoc `Para` or `Header`.

Therefore, when an unsupported inline node or mark occurs, the ADF reader
retains the smallest enclosing ADF block node as one `atlas_doc_format` marker.
For example, a paragraph containing a `status` node becomes a fence containing
the complete original `paragraph` JSON. This is lossless, but the rest of that
paragraph is not independently editable as GFM.

For unsupported content inside a list item, table cell, panel, or other
container, the reader retains the smallest enclosing node that is valid in the
same parent position. The writer validates the recovered node against that
parent's allowed ADF child types. If no such block boundary exists, the reader
retains the nearest valid ancestor rather than changing the document shape.

### Initially opaque ADF features

- `status`, `mention`, `date`, `emoji`, `inlineCard`, and unsupported media.
- `panel`, `expand`, `nestedExpand`, task and decision lists, layouts, and
  `extensionFrame`.
- `extension`, `bodiedExtension`, `multiBodiedExtension`, sync blocks, and
  third-party Confluence macro nodes.
- `textColor`, `underline`, `subsup`, `border`, `alignment`, `breakout`, and
  other unhandled marks or attributes.
- Unsupported table geometry, captions, annotations, and collaboration data.

Each feature can later be promoted to a readable GFM mapping only when the
reverse mapping, supported attribute set, and round-trip tests are defined.

## Attachment interaction

The ADF/Pandoc converter does not download or upload attachment bytes. It
uses a `MediaResolver` supplied by the synchronization layer:

```text
ADF media identifier ⇄ managed attachment ID ⇄ _attachments/filename
```

The synchronization layer maintains that relationship in the page cache and
performs the attachment API operations. A conversion fails instead of emitting
a guessed local path when the relationship is absent or ambiguous.

## Required tests

- ADF → Pandoc AST → ADF semantic round trips for every direct mapping.
- Pandoc GFM → AST → ADF → AST → GFM round trips for the supported GFM subset.
- Exact JSON preservation for opaque markers, including an inline `status`
  node retained through its enclosing paragraph.
- Parent-context rejection for malformed or misplaced opaque JSON.
- Attachment resolver tests for image and file references, ambiguity, and path
  traversal rejection.
- Rejection of unsupported attributes rather than silent loss.
