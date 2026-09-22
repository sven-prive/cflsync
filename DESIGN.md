# cflsync: single-page synchronization design

## Scope

Each synchronization operation targets exactly one Confluence Cloud page. It
does not discover, download, or update child pages. The `.cflsync` directory
is at the workarea root, separate from managed page content. The command,
storage, and synchronization contracts are specified in [SPEC.md](SPEC.md).

## Implementation constraints

Minimize external dependencies. Prefer the Python standard library whenever it
provides the required capability: use `urllib` for HTTP, `json` for JSON,
`pathlib` and `tempfile` for filesystem operations, `hashlib` for content
hashes, and `unittest` for tests unless an external dependency provides a
clear, necessary capability that the standard library lacks.

Use `uv` for all Python dependency management. Add or change dependencies via
`uv`, record declared runtime dependencies in `pyproject.toml`, and commit the
corresponding `uv.lock` update. Do not install unmanaged project dependencies
with `pip` or rely on globally installed Python packages. Pandoc is an
external executable prerequisite, not a Python package dependency.

Prefer small, module-level classes with shallow inheritance. Use duck typing
for internal collaborations when a formal abstraction adds no behavioral
guarantee. Add type annotations for public contracts and non-obvious data
shapes; avoid elaborate generic, protocol, or class hierarchies without a
concrete interoperability need.

Separate every conditional or loop block from a following statement at the
same indentation with a blank line. Avoid conditional expressions for returns.

## Conversion strategy

Page bodies use `atlas_doc_format` as the Confluence transport format. The
conversion boundary is:

```text
atlas_doc_format ⇄ Pandoc AST ⇄ GFM
```

Pandoc provides the GFM reader and writer. The Python implementation exposes
`ADFToMarkdownConverter` and `MarkdownToADFConverter`, using Pandoc's JSON
AST internally. The current Markdown dialect is GFM. The converters have no
cache, remote-page, or workspace state.

On pull, the ADF body becomes canonical GFM for `page.md`. On push, `page.md`
becomes ADF for the versioned API update. The synchronization coordinator owns
cache state, staging, attachment resolution, and concurrent-edit handling.

`PandocRunner` invokes a pinned compatible Pandoc binary through argument
lists rather than a shell and verifies the expected Pandoc JSON API version.
`MediaResolver` is supplied by the synchronization coordinator. It maps ADF
media identifiers to the managed attachment IDs and local `_attachments/`
paths recorded in the page cache.

Only supported constructs receive a native GFM mapping. Unsupported ADF nodes
and marks are retained as `atlas_doc_format` fenced blocks containing complete
ADF-node JSON. For unsupported inline content, the smallest enclosing ADF
block is retained so that the fence remains valid GFM. Malformed, altered, or
contextually invalid retained JSON stops a push before any API request.

The full mapping, opaque-marker format, and required conversion tests are in
[MAPPING.md](MAPPING.md).

## Deferred functionality

- Child-page/subtree synchronization and page-link resolution.
- Automatic three-way merge or conflict markers.
- Arbitrary Confluence macros and unsupported ADF constructs.
- Attachment rename tracking beyond delete-and-upload semantics.
- Bidirectional synchronization of page metadata other than title and body.
