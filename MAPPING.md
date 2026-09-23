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

ADF is the remote source of truth. Conversion is intentionally lossy: supported
content becomes readable GFM, while unrelated metadata and unsupported
formatting are omitted. Unsupported structures are retained as complete ADF
JSON in a Pandoc code block, which Pandoc writes as a fenced GFM block.

## Conversion invariants

- Every emitted ADF document has `type: "doc"` and `version: 1`.
- Supported node handlers consume only the fields needed for conversion.
  Extra fields and attributes do not alone cause opaque fallback.
- Required values and content shapes remain validated. Unsupported node types
  and structures retain their original JSON, including metadata.
- Ignored attributes and formatting are not recovered by reverse conversion;
  a subsequent push may discard them remotely.
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

ADF page metadata remains in the cflsync page cache rather than Pandoc
metadata. For page conversion, the caller supplies the title from page
metadata; it becomes the first and only level-one Markdown heading. Body
level-one headings become level two; levels two through six remain unchanged.
The converter itself has no cache access. Pull supplies the fetched title
that will be recorded in the cache, including on first pull and title changes.
Body-only conversion may omit the title.

The generated title heading is presentation metadata, not an ADF body node.
Reverse conversion removes it when the caller supplies the title, and rejects a
heading that no longer matches, because push does not rename pages.

## Direct block mappings

| ADF node | Pandoc AST | Reverse ADF node |
| --- | --- | --- |
| `paragraph` | `Para` | `paragraph` |
| `heading` with `attrs.level` 1–6 | `Header max(2, level)` | `heading` with `attrs.level` |
| `blockquote` | `BlockQuote` | `blockquote` |
| `panel` | GFM alert `Div` | canonical `panel` |
| `bulletList` and `listItem` | `BulletList` | `bulletList` and `listItem` |
| `orderedList` and `listItem` | `OrderedList` | `orderedList` and `listItem` |
| `taskList` and `taskItem` | `BulletList` beginning each item with `☐` or `☒` | `taskList` and `taskItem` |
| `codeBlock` | `CodeBlock`; `attrs.language` becomes its language class | `codeBlock` with `attrs.language` |
| `rule` | `HorizontalRule` | `rule` |

Handlers validate and convert meaningful fields such as heading level,
ordered-list start number, and code language. Other attributes, including
`localId` and presentation attributes, are ignored for these supported nodes.
Reading GFM gives every heading an implicit Pandoc identifier, which the reverse
mapping ignores since ADF has no counterpart.
A paragraph with omitted or empty `content` converts to an empty paragraph,
which Pandoc omits from canonical GFM. Malformed required values cause opaque
fallback; malformed document structure may instead raise a conversion error.

Blockquotes map to Markdown `>` blocks and back to ADF `blockquote` nodes,
with their contained blocks and inline formatting converted recursively.

Panels map to GFM alerts and back to canonical panel types:

| ADF `panelType` | GFM alert | Reverse `panelType` |
| --- | --- | --- |
| `info` | `NOTE` | `note` |
| `note` | `NOTE` | `note` |
| `tip` | `TIP` | `tip` |
| `warning` | `WARNING` | `warning` |
| `error` | `CAUTION` | `error` |
| `success` | `TIP` | `tip` |
| `custom` | `NOTE` | `note` |

The mapping discards `panelColor`, panel icon attributes, and `localId`.
Only the Pandoc alert `Div` shape emitted by GFM alert syntax is recognized as
a panel on reverse conversion; ordinary blockquotes remain blockquotes.

Task lists map directly to GFM task lists: `TODO` becomes `- [ ]` and `DONE`
becomes `- [x]`. Pandoc represents these markers as leading `☐` and `☒` inline
nodes in a `BulletList`. A nested task list remains nested under its preceding
task item. Reverse conversion emits no task-list or task-item `localId`.

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
| `emoji` with `attrs.text` | `Str` holding that text | `text` |
| `mention` with `attrs.id` | raw HTML `span` | `mention` |
| `date` with a millisecond timestamp | raw HTML `span` | `date` |
| `status` | raw HTML `span` | `status` |

Supported text marks are emitted in a deterministic nesting order. Other marks
are ignored while retaining their text and supported marks. Extra fields on
supported marks are ignored, but required values such as a link's non-empty
string destination remain validated. Malformed or duplicate supported marks
cause retention of the enclosing block. Code can be combined with the other
supported marks.

## Tables and media

`table`, `tableRow`, `tableHeader`, and `tableCell` map to a Pandoc `Table`.
Pandoc then picks the GFM representation: a pipe table when every cell holds a
single paragraph and all spans are 1, and an HTML `<table>` otherwise. The
reverse direction reads a pipe table directly; an HTML table arrives as a raw
block and is parsed back into a Pandoc `Table` by a second Pandoc invocation,
after which both representations share one mapping. Only raw blocks that are
HTML tables are accepted; other raw content has no ADF equivalent.

Cell content uses the ordinary block mapping, so opaque markers inside a cell
are retained like anywhere else. An ADF table converts unless its structure is
invalid; a leading row of `tableHeader` cells becomes the table head, and a
table without one is written with an empty header row, which becomes a real
empty header row when pushed back.

These table features do not survive conversion: header cells outside the first
row, hard breaks inside a cell, table `layout`, `width`, and `localId`, cell
`colwidth` and `background`, `isNumberColumnEnabled`, and column alignment,
which has no ADF counterpart in either direction.

`mediaSingle` and `mediaGroup` map to a Pandoc paragraph of `Image` or `Link`
inlines when the page attachment manifest resolves the ADF media identifier to
a managed local `_attachments/<filename>` path. `Image` is used for filenames
with an image suffix and `Link` otherwise, since the ADF media node carries no
media type. External media uses its own URL and needs no manifest. The reverse
mapping uses the manifest to reconstruct the corresponding ADF media node.
Media that the manifest cannot resolve stays opaque. Layout, width, and height
attributes are dropped.

`mediaInline` maps to a Pandoc `Image` or `Link` inside its paragraph, and an
image that shares a paragraph with other content maps back to `mediaInline`. An
image alone in a paragraph is `mediaSingle` in both directions, so a lone
`mediaInline` becomes `mediaSingle` after a round trip. An inline image outside
`_attachments/` is rejected, since ADF inline media has no external form.

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

An emoji becomes its Unicode text, so it reads as an ordinary character and
pushes back as a `text` node rather than an `emoji` node; `shortName` and `id`
are dropped. A custom emoji carries no `attrs.text` and is retained opaquely,
keeping its identity. Reading GFM turns a typed `:shortcode:` into a Pandoc
emoji span, which the reverse mapping accepts and reduces to the same Unicode
text; other spans have no ADF form.

A date becomes `<span cflsync-type="date" cflsync-timestamp="TIMESTAMP">LOCAL-DATE</span>`.
`LOCAL-DATE` is derived from `TIMESTAMP` in the machine-local timezone. Reverse
conversion parses the span through Pandoc and accepts only the exact attributes
and a date text matching the local date for that timestamp. The original
timestamp is then retained. A malformed timestamp retains its enclosing block
opaquely.

A status becomes `<span cflsync-type="status" style="background-color: COLOR">TEXT</span>`.
ADF `neutral` uses CSS `gray`; every other supported ADF status color uses the
same CSS name. Reverse conversion parses this raw HTML through Pandoc and
accepts only one plain-text status span with those exact attributes. Renderers
may sanitize the custom attribute or style, but the Markdown source remains
reversible for cflsync.

A mention becomes `<span cflsync-type="mention" cflsync-id="ACCOUNT-ID">TEXT</span>`.
When present, `accessLevel` and `userType` become `cflsync-access-level` and
`cflsync-user-type` attributes. `localId` is ignored. Reverse conversion parses
the span through Pandoc and requires a non-empty account ID; it performs no
name or email lookup. Plain Markdown has no mention-creation syntax.

ADF has inline nodes such as `inlineCard`. A GFM fence is a block construct and cannot
occupy a position inside a Pandoc `Para` or `Header`.

Therefore, when an unsupported inline node occurs, the ADF reader
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

- `inlineCard`, custom emoji, and unsupported media.
- `expand`, `nestedExpand`, decision lists, layouts, and
  `extensionFrame`.
- `extension`, `bodiedExtension`, `multiBodiedExtension`, sync blocks, and
  third-party Confluence macro nodes.
- Tables, including multi-paragraph cells and unsupported geometry.

Each feature can later be promoted to a readable GFM mapping only when the
structural mapping and reverse conversion are defined. Formatting marks such
as `textColor`, `underline`, and `subsup`, and decorative attributes such as
alignment, do not by themselves trigger opaque retention on supported nodes.

## Attachment interaction

The ADF/Pandoc converter does not download or upload attachment bytes. It
uses a `MediaResolver` supplied by the synchronization layer:

```text
ADF media identifier ⇄ attachment file ID ⇄ _attachments/filename
```

ADF media nodes reference an attachment by its file ID, which the Confluence
attachment manifest reports as `fileId`. That value differs from the attachment
ID used by the attachment API operations, so the media manifest is keyed on the
file ID. The synchronization layer performs the attachment API operations and
supplies the manifest; it never emits a guessed local path. `MediaResolver`
receives ordered `(filename, file ID)` manifest entries and exposes the two pure
lookups `path_for()` and `id_for()`.

## Required tests

- ADF → Pandoc AST → ADF semantic round trips for every direct mapping.
- Pandoc GFM → AST → ADF → AST → GFM round trips for the supported GFM subset.
- Exact JSON preservation for opaque markers, including an inline `status`
  node retained through its enclosing paragraph.
- Parent-context rejection for malformed or misplaced opaque JSON.
- Attachment resolver tests for image and file references, ambiguity, and path
  traversal rejection.
- Tolerance of extra metadata and formatting on supported nodes without
  mutating the source document.
- Validation of required values and exact retention of unsupported structures.
