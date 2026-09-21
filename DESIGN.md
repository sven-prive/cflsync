# cflsync: single-page synchronization design

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

## Scope

Each synchronization operation targets exactly one Confluence Cloud page. It
does not discover, download, or update child pages. The `.cflsync` directory
is at the workarea root, separate from managed page content. All sync state
is retained below this directory, allowing future multi-page support without
putting private state in page directories. The initial implementation manages
one page directory; the layout is:

```
<workarea>/
  .cflsync/
    profile
    cache/
      123456.json
  <page-title>/
    page.md
    _attachments/
      <attachment filename>
```

`page.md` contains GitHub Flavored Markdown (GFM). `_attachments` contains
the current downloaded attachment files. The workarea-level `.cflsync/profile`
contains the selected credential profile. `.cflsync/cache/<page-id>.json` is
private per-page synchronization state and must not be interpreted as page
content.

The initial command surface is deliberately small:

```
cflsync auth [-p PROFILE] [--list | --delete]
cflsync init [-p PROFILE]
cflsync page create PARENT_PAGE_ID TITLE
cflsync page pull PAGE_REF
cflsync page push PAGE_REF
cflsync page status PAGE_REF
```

`init` initializes the current directory as a workarea only: it creates
`.cflsync/`, creates `.cflsync/cache/`, and records the selected profile in
`.cflsync/profile`. It neither resolves nor records a Confluence page.
`page pull` resolves `PAGE_REF` and creates or updates that page's directory
and cache entry. `page push` operates on the identified page and finds its
local state at `.cflsync/cache/<page-id>.json`.

`page create PARENT_PAGE_ID TITLE` creates an empty Confluence page under the
given parent, then runs the equivalent of `page pull` for the returned page
ID. It has no offline mode. Requiring Confluence to create the page ensures
that every local page begins with an authoritative page ID, version, title,
and other service-created metadata.
Commands locate the workarea by walking upward to a directory containing
`.cflsync/profile`.

## Page references

`page pull`, `page push`, and `page status` accept one `PAGE_REF`. A reference
may be a numeric Confluence page ID, a page title, a local GFM file, or a local
page directory. The resolver classifies the argument in this order:

1. An existing filesystem path is a local reference. A file must be the
   managed `page.md`; a directory must contain that file. The enclosing page
   directory is then mapped to its cache entry, which supplies the page ID.
2. A non-path argument composed only of decimal digits is a Confluence page
   ID.
3. Any other argument is a page title.

Path references must lie within the discovered workarea and identify a page
that already has a cache entry. They cannot address arbitrary standalone GFM
files. This prevents an accidental push from creating an untracked page.

An ID is resolved directly through Confluence. A title first matches a cached
page title in the workarea; if no cached match exists, it is looked up in
Confluence. Both cases require exactly one matching page. Zero matches and
multiple exact-title matches are errors; ambiguity is reported with the
available page IDs. A title reference therefore never selects an arbitrary
same-titled page.

All resolved references produce a page ID before synchronization. The command
semantics, cache keys, concurrency checks, and conflict handling thereafter
are identical regardless of the reference form.

## Local representation

The page directory name is derived from the remote title with a deterministic
filesystem-safe escaping rule. It is presentation only; the Confluence page
ID and page-directory name in `.cflsync/cache/<page-id>.json` are
authoritative. A title change on pull renames the page directory when the
target name is unused. If it is occupied, pull stops and reports the collision
rather than overwriting local data.

Attachments are addressed in Markdown with relative URLs:

```markdown
![Diagram](_attachments/diagram.png)
[Download spreadsheet](_attachments/report.xlsx)
```

Conversion from ADF to GFM rewrites resolved attachment media and links to
these relative paths. Conversion in the other direction recognizes only
relative paths rooted at `_attachments/`; `MediaResolver` turns them into the
corresponding Confluence attachment media references. Other links remain
ordinary external or page links. Attachment filenames are normalized to
prevent path traversal and duplicate names are disambiguated deterministically.

An attachment is part of the synchronized page state. On pull, the remote
attachment manifest determines which local files are added, updated, or
removed. On push, files currently under `_attachments/` are uploaded or
updated, and previously synchronized files that were removed locally are
deleted remotely. Attachments not managed by cflsync must not be deleted;
the manifest identifies managed remote attachment IDs.

## Per-page cache entry

Each synchronized page has one cache entry at
`.cflsync/cache/<page-id>.json`, written atomically with mode `0600`. The
workarea's `.cflsync` directory and its `cache` directory have mode `0700`.
The JSON state file contains no credentials. Page IDs are stable cache keys,
so the layout extends to multiple pages without title-based collisions. A
representative state-file shape is:

```json
{
  "format": 1,
  "page": {
    "id": "123456",
    "title": "Example page",
    "directory": "Example page",
    "version": 17,
    "content_hash": "..."
  },
  "attachments": {
    "diagram.png": {
      "id": "987654",
      "version": 3,
      "content_hash": "..."
    }
  }
}
```

For state format `1`, `content_hash` is a SHA-256 digest of canonical GFM for
the page and of raw file bytes for an attachment. The hash algorithm is part
of the state-format definition rather than the field name. A future algorithm
change requires a new `format` version and cache migration or replacement.
The cached versions and content hashes describe the last state known to be
identical locally and remotely.

## Change detection and synchronization

`page status PAGE_REF` compares the identified page's current local
representation to `.cflsync/cache/<page-id>.json` and fetches current remote
page and attachment metadata. It reports each side as unchanged or changed,
without modifying the workarea.

Before either modifying operation, cflsync computes two independent changes:

| State since last successful sync | Meaning |
| --- | --- |
| The canonical GFM content hash or any managed attachment content hash differs from its cached value | local change |
| Confluence page version or managed attachment version differs from the cached version | remote change |

The decision table applies to the page and its complete managed attachment
set as one atomic synchronization unit:

| Local | Remote | `page pull` | `page push` |
| --- | --- | --- | --- |
| unchanged | unchanged | no-op | no-op |
| unchanged | changed | replace local page and managed attachments | conflict |
| changed | unchanged | conflict | upload local page and attachment set |
| changed | changed | conflict | conflict |

A conflict does not alter local files, remote content, or the page cache
entry. The user resolves it by saving desired content locally and using a
future explicit resolution command; automatic merging is outside this scope.
A remote update uses the current Confluence page version, and a version
mismatch response is treated as a conflict even if the earlier check found no
change.

`page pull` stages downloads in a temporary directory, converts the page,
validates all attachment paths and downloaded data, then swaps the local
representation and cache entry only after every operation succeeds. `page
push` uploads changed attachments, updates page content with optimistic
concurrency, applies managed attachment deletions, and writes its cache entry
only on complete success. Failed remote sequences are reported as incomplete;
the next `page status` detects the resulting remote change rather than
assuming a successful sync.

## Conversion strategy

Page bodies use `atlas_doc_format` as the Confluence transport format. The
conversion boundary is:

```text
atlas_doc_format ⇄ Pandoc AST ⇄ GFM
```

Pandoc provides the GFM reader and writer. The Python implementation maps
between ADF and Pandoc's JSON AST, keeping Confluence API integration, ADF
schema validation, cache access, and attachment resolution in one runtime.

On pull, `ADFReader` decodes the API's JSON-encoded ADF body and produces a
Pandoc JSON AST; Pandoc writes canonical GFM to `page.md`. On push, Pandoc
parses `page.md` to its JSON AST; `ADFWriter` produces ADF, validates it, and
passes its JSON-encoded form to `APIClient` for the versioned update.

`PandocRunner` invokes a pinned compatible Pandoc binary through argument
lists rather than a shell and verifies the expected Pandoc JSON API version.
`MediaResolver` maps ADF media identifiers to the managed attachment IDs and
local `_attachments/` paths recorded in the page cache.

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
