# cflsync specification

## Command interface

```text
cflsync auth [-p PROFILE] [--list | --delete]
cflsync init [-p PROFILE] ROOT_PAGE_REF
cflsync page create PARENT_PAGE_REF TITLE
cflsync page pull [-f | --force] PAGE_REF
cflsync page push [-f | --force] PAGE_REF
cflsync page rename PAGE_REF TITLE
cflsync page move PAGE_REF NEW_PARENT_REF
cflsync page remove [-f | --force] PAGE_REF
cflsync page status PAGE_REF
```

`init` resolves `ROOT_PAGE_REF`, a page ID or an exact page title, through
Confluence with the profile's credentials. It then creates `.cflsync/` with an
empty `cache/`, `profile`, and `root`, which records the root page ID, as one
atomic installation. A failed lookup leaves no `.cflsync/` behind. `init`
refuses a directory inside any existing workarea, and does not pull pages.
`page pull` resolves
`PAGE_REF` and creates or updates its page directory and cache entry. `page
push` finds the identified page's local state at
`.cflsync/cache/<page-id>.json`.

`page rename PAGE_REF TITLE` requires the referenced managed page to be in
sync. It updates the remote title with optimistic concurrency, rewrites the
generated title heading, renames the title-derived local directory, and writes
the updated cache state. `TITLE` must be non-empty, single-line text without
surrounding whitespace. It is the explicit local-title operation; `page push`
continues to reject an edited title heading.

`page move PAGE_REF NEW_PARENT_REF` requires the referenced managed page to be
in sync. `NEW_PARENT_REF` resolves to a remote page, which need not be managed
locally. The command changes the remote parent but leaves the current
loose-page workarea's directory and Markdown unchanged.

`page remove [-f | --force] PAGE_REF` requires a managed local page directory
and cache entry. It confirms removal unless `--force` is supplied. When the
remote page exists, the command requires it to be synchronized, deletes it,
then removes the local directory and cache entry. A remote 404 is treated as
an already-removed remote page, so only the local copy is removed.

`page create PARENT_PAGE_REF TITLE` resolves `PARENT_PAGE_REF`, creates an
empty child page remotely, then runs the equivalent of `page pull` for its
returned ID. It has no offline mode, so each local page begins with
Confluence-authoritative metadata. Commands locate a workarea by walking upward
to a directory containing `.cflsync/profile`. A workarea without a valid
`.cflsync/root` is a version-1 workarea; every command except `auth` refuses
it and explains how to create a new, anchored workarea.

## Page references

`page create`, `page pull`, `page push`, `page rename`, `page move`, and `page
status` accept a page reference: a numeric page ID, page title, local GFM file,
or local page directory. The resolver classifies the argument in this order:

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

`page remove` accepts the same forms, but each must resolve to cached local
state; it does not resolve an ID or title remotely.

## Workarea and local representation

All synchronization state is below `.cflsync`, separate from managed content,
so it can later support multiple pages without private state in page
directories. The initial layout is:

```
<workarea>/
  .cflsync/
    profile
    root
    cache/
      123456.json
  <page-title>/
    page.md
    _attachments/
      <attachment filename>
```

`page.md` contains GitHub Flavored Markdown (GFM); `_attachments` contains
downloaded attachment files. `.cflsync/profile` selects the credential profile.
`.cflsync/root` holds the root page ID as one numeric line.
`.cflsync/cache/<page-id>.json` is private synchronization state, not page
content.

The page directory name is a deterministic filesystem-safe encoding of the
remote title. It is presentation only: the cache's page ID and directory name
are authoritative. A pull renames a changed title only if its target is unused;
otherwise it stops without overwriting data.

On Windows, a title change cannot rename the page directory when cflsync is
running from inside that directory. The command stops before mutation and asks
the user to run it from the workarea or another directory before retrying.

Attachments use relative Markdown URLs:

```markdown
![Diagram](_attachments/diagram.png)
[Download spreadsheet](_attachments/report.xlsx)
```

ADF-to-GFM conversion rewrites resolved media and links to these paths. The
reverse conversion recognizes only paths rooted at `_attachments/`; a
`MediaResolver` maps them to Confluence attachment references. Other links stay
ordinary links. Filenames are validated to prevent traversal, and duplicate
manifest names or attachment IDs are rejected as ambiguous.

On pull, the remote attachment manifest determines managed local files. A local
file under `_attachments/` that `page.md` links to also becomes managed, so new
attachments can be introduced locally; files that nothing links to stay
unmanaged. On push, managed files are uploaded or updated and previously
managed files removed locally are deleted remotely. Attachments outside the
managed set must not be deleted.

## Per-page cache entry

On Unix, each synchronized page has an atomically written `0600` cache entry
at `.cflsync/cache/<page-id>.json`; `.cflsync` and `cache` use mode `0700`.
Cache entries contain no credentials. Credential configuration is also
atomically written with mode `0600` under a mode-`0700` configuration
directory. On Windows, POSIX modes do not apply. Credentials remain below the
per-user configuration directory selected by `platformdirs`, relying on its
default user ACL; cflsync does not alter Windows ACLs. Stable page IDs are
cache keys, avoiding title-based collisions. Failed or interrupted
initialization, staging, and private-file writes remove their temporary files
or directories. Format 1 is:

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
      "id": "att987654",
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

GET and HEAD requests retry once after a transport failure or HTTP 429, 502,
503, or 504 response. POST, PUT, and DELETE requests are never retried
implicitly, because their remote effects may be indeterminate after a failed
request.

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

A conflict does not alter local files, remote content, or the cache entry.
Explicit force options select which side wins; automatic merging is outside
scope. Remote updates use the current Confluence page version. A version
mismatch is a conflict even if an earlier check found no change.

An unchanged pull reports that local and remote content are already in sync
and nothing was pulled. `page pull -f` (or `--force`) bypasses this no-op and
downloads and regenerates the local representation, for example after a
converter update. It also resolves conflicts in favor of the remote version:
local changes to managed files are overwritten, and missing managed files are
restored. Unmanaged files remain protected, and failed pulls retain the previous
local files and cache. `page push -f` (or `--force`) is the mirror image: it
bypasses the push no-op and resolves conflicts in favor of local content,
uploading it over remote changes. The update still uses the current Confluence
version, so a concurrent edit between the check and the update is a conflict.

Push never writes local files. It uploads new and changed managed attachments,
converts `page.md` to ADF, updates the page, and deletes previously managed
attachments removed locally, in that order. The title heading that pull adds is
removed before conversion and is not part of the body; editing it is rejected,
because push does not rename pages.

`page rename` is non-destructive but changes the local path. It first checks
that page and attachment state is unchanged locally and remotely, and rejects
a target directory that is occupied or cached for another page. It stages the
rewritten Markdown before the remote update, preserves the current ADF body in
that update, then renames the directory and writes cache last. If the remote
update succeeds but local installation or the cache write fails, the command
reports incomplete synchronization; the old cache makes the remote title change
visible to `status` and recoverable with `pull`.

`page move` checks that the managed source page and attachments are unchanged
locally and remotely. It resolves and fetches the new parent, which must be a
different page in the same space. A move sends the existing title and ADF body
with the new `parentId` in one versioned update. Confluence validates the
resulting hierarchy; a rejected update identifies both the source and target
page IDs. The local directory and page content remain unchanged; cache state
records the returned page version last. If cache writing fails after the remote
move, the command reports incomplete synchronization and a pull can recover it.

`page remove` resolves only local managed state, checks the remote page if it
still exists, and asks for confirmation immediately before deletion. `--force`
only bypasses that prompt. A remote deletion failure leaves the local directory
and cache intact. After a successful remote deletion, it removes the complete
local directory, including unmanaged files, then its cache entry. If the remote
page is already absent, it performs that local cleanup without a remote delete.

`page pull` stages downloads, conversion, attachment-path validation, and
content validation in a temporary directory. For an existing page it preserves
the page and attachment directories, atomically replaces `page.md` and each
managed attachment, and writes the cache last. Only a title change renames the
existing page directory. Backups allow rollback of file changes and the rename
if installation or the cache write fails. These individual replacements are
not a single filesystem transaction across all files. `page push` uploads changed
attachments, updates content with optimistic concurrency, applies managed
attachment deletions, and writes cache only after complete success. A failed
remote sequence is reported as incomplete; the next `page status` detects the
resulting remote change.

## Content hierarchy requests

The API client lists a page's ancestors and direct children. No command uses
these listings yet; they support the planned subtree operations. Their
behaviour was verified against Confluence Cloud:

- `GET /pages/{id}/ancestors` returns `id` and `type` for every ancestor,
  highest first. Non-page ancestors such as folders are included. A response
  holds at most `limit` ancestors, nearest to the requested content, and has no
  `next` link. `APIClient.page_ancestors` requests the maximum `limit` of 250.
  It continues a full response from its highest ancestor, through
  `/pages/{id}/ancestors` or `/folders/{id}/ancestors`, and rejects any other
  ancestor type as a continuation point.
- `GET /pages/{id}/direct-children` returns `id`, `status`, `title`, `type`,
  and `childPosition`, in position order, and is paginated through
  `_links.next`. According to the API reference, it includes non-page children
  (folders, whiteboards, databases, embeds). The `/pages/{id}/children`
  endpoint omits `type`, so it cannot distinguish non-page children, and is not
  used.
- A `limit` above 250 is rejected with HTTP 400 by both endpoints.
