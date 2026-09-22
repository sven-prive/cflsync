# cflsync specification

## Command interface

```text
cflsync auth [-p PROFILE] [--list | --delete]
cflsync init [-p PROFILE]
cflsync page create PARENT_PAGE_ID TITLE
cflsync page pull PAGE_REF
cflsync page push PAGE_REF
cflsync page status PAGE_REF
```

`init` creates `.cflsync/`, `.cflsync/cache/`, and `.cflsync/profile`; it
neither resolves nor records a Confluence page. `page pull` resolves
`PAGE_REF` and creates or updates its page directory and cache entry. `page
push` finds the identified page's local state at
`.cflsync/cache/<page-id>.json`.

`page create PARENT_PAGE_ID TITLE` creates an empty child page remotely, then
runs the equivalent of `page pull` for its returned ID. It has no offline mode,
so each local page begins with Confluence-authoritative metadata. Commands
locate a workarea by walking upward to a directory containing `.cflsync/profile`.

## Page references

`page pull`, `page push`, and `page status` accept one `PAGE_REF`: a numeric
page ID, page title, local GFM file, or local page directory. The resolver
classifies the argument in this order:

1. An existing filesystem path is a local reference. A file must be managed
   `page.md`; a directory must contain that file. Its cache entry supplies the
   page ID.
2. A non-path argument containing only decimal digits is a Confluence page ID.
3. Any other argument is a page title.

Path references must be inside the discovered workarea and identify a cached
page; arbitrary standalone GFM files are rejected. An ID is resolved directly
through Confluence. A title first matches a cached title; otherwise it is
looked up remotely. Both must produce exactly one page. Zero and multiple
matches are errors, and ambiguity reports available page IDs.

All successful reference forms produce a page ID. Subsequent command
semantics, cache keys, concurrency checks, and conflict handling are identical.

## Workarea and local representation

All synchronization state is below `.cflsync`, separate from managed content,
so it can later support multiple pages without private state in page
directories. The initial layout is:

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

`page.md` contains GitHub Flavored Markdown (GFM); `_attachments` contains
downloaded attachment files. `.cflsync/profile` selects the credential profile.
`.cflsync/cache/<page-id>.json` is private synchronization state, not page
content.

The page directory name is a deterministic filesystem-safe encoding of the
remote title. It is presentation only: the cache's page ID and directory name
are authoritative. A pull renames a changed title only if its target is unused;
otherwise it stops without overwriting data.

Attachments use relative Markdown URLs:

```markdown
![Diagram](_attachments/diagram.png)
[Download spreadsheet](_attachments/report.xlsx)
```

ADF-to-GFM conversion rewrites resolved media and links to these paths. The
reverse conversion recognizes only paths rooted at `_attachments/`; a
`MediaResolver` maps them to Confluence attachment references. Other links stay
ordinary links. Filenames are normalized to prevent traversal and duplicates
are disambiguated deterministically.

On pull, the remote attachment manifest determines managed local files. On
push, existing managed files are uploaded or updated and previously managed
files removed locally are deleted remotely. Attachments outside the managed
manifest must not be deleted.

## Per-page cache entry

Each synchronized page has an atomically written `0600` cache entry at
`.cflsync/cache/<page-id>.json`; `.cflsync` and `cache` use mode `0700`. Cache
entries contain no credentials. Stable page IDs are cache keys, avoiding
title-based collisions. Format 1 is:

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

For format 1, `content_hash` is SHA-256 of canonical GFM for pages and raw
bytes for attachments. The algorithm is part of the format definition; an
algorithm change requires a new format and cache migration or replacement.
Cached versions and hashes describe the last state known to be identical
locally and remotely.

## Change detection and synchronization

`page status PAGE_REF` compares local content with its cache entry and fetches
remote page and attachment metadata. It reports each side as unchanged or
changed without modifying the workarea.

Before either modifying operation, cflsync computes two independent changes:

| State since last successful sync | Meaning |
| --- | --- |
| Canonical GFM or a managed attachment hash differs from cache | local change |
| Confluence page or managed attachment version differs from cache | remote change |

The page and its complete managed attachment set are one atomic unit:

| Local | Remote | `page pull` | `page push` |
| --- | --- | --- | --- |
| unchanged | unchanged | no-op | no-op |
| unchanged | changed | replace local page and managed attachments | conflict |
| changed | unchanged | conflict | upload local page and attachment set |
| changed | changed | conflict | conflict |

A conflict does not alter local files, remote content, or the cache entry. A
future explicit resolution command handles it; automatic merging is outside
scope. Remote updates use the current Confluence page version. A version
mismatch is a conflict even if an earlier check found no change.

`page pull` stages downloads, conversion, attachment-path validation, and
content validation in a temporary directory, then swaps local representation
and cache only after every operation succeeds. `page push` uploads changed
attachments, updates content with optimistic concurrency, applies managed
attachment deletions, and writes cache only after complete success. A failed
remote sequence is reported as incomplete; the next `page status` detects the
resulting remote change.
