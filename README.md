# cflsync

`cflsync` is a command-line tool for synchronizing Confluence Cloud pages with
local GitHub Flavored Markdown (GFM) page directories and their attachments.
The goal is local authoring of wiki pages in markdown format, using Confluence
as publishing platform rather than authoring environment.

## Documentation

- [Design](doc/DESIGN.md): implementation constraints, scope, and architectural
  decisions.
- [Specification](doc/SPEC.md): command behavior, local storage, and
  synchronization rules.
- [Mapping](doc/MAPPING.md): `atlas_doc_format` to Pandoc AST mapping and
  opaque retention strategy.

## Prerequisites

- Python 3.11 or later.
- [uv](https://docs.astral.sh/uv/) for Python dependency management.
- Pandoc for markup conversion.

## Installation

Install `cflsync` as a standalone command in its own environment:

```console
uv tool install git+https://github.com/sven-prive/cflsync
```

From a local checkout, `uv tool install .` does the same. Either way the
`cflsync` executable lands on `PATH`; Pandoc is not bundled and must be
installed separately. Without installing, the same interface is available from
a checkout as `uv run python -m cflsync`.

## Commands:

```text
cflsync auth [-p PROFILE] [--list | --delete]
cflsync init [-p PROFILE]
cflsync page create PARENT_PAGE_ID TITLE
cflsync page pull [-f | --force] PAGE_REF
cflsync page push [-f | --force] PAGE_REF
cflsync page status PAGE_REF
```

`PAGE_REF` may be a numeric Confluence page ID, an exact page title, a managed
local `page.md` file, or a managed page directory.

Pull reports when the page is already in sync. Use `page pull --force PAGE_REF`
to prefer remote content, overwriting local edits to managed files even when
the remote version is unchanged. Unmanaged files are preserved.

Push reports when there is nothing to upload, and refuses to overwrite remote
changes. Use `page push --force PAGE_REF` to prefer local content. The first
heading of `page.md` is the page title and cannot be edited; push does not
rename pages.

Use `cflsync --help` for top-level help, `cflsync page --help` for page-command
help, and `cflsync page COMMAND --help` for a command's arguments.

## Development

YAPF is the project formatter, set up to align closely with PEP-8 style. Apply
formatting before committing changes:

```console
uv run --group dev python -m yapf --recursive --in-place cflsync tests
```

Check formatting without modifying files:

```console
uv run --group dev python -m yapf --recursive --diff cflsync tests
```

## Testing

Run the complete test suite with:

```console
uv run python -m unittest discover -s tests -v
```

The automated suite uses only local filesystem fixtures and recorded HTTP
transports. Importing the `tests` package blocks address resolution and socket
connections, so an accidental live request fails instead of reaching a real
site.

### Manual acceptance check

Live acceptance against Confluence Cloud is a manual operation and is never
part of the automated suite. Run it against a disposable page in a scratch
space, using a workarea outside the repository:

```console
cflsync auth -p acceptance
mkdir /tmp/cflsync-acceptance && cd /tmp/cflsync-acceptance
cflsync init -p acceptance
cflsync page create PARENT_PAGE_ID "cflsync acceptance"
cflsync page status "cflsync acceptance"
$EDITOR "cflsync acceptance/page.md"
cflsync page push "cflsync acceptance"
cflsync page pull "cflsync acceptance"
cflsync page status "cflsync acceptance"
```

The check passes when the edit appears on the remote page, the final status
reports both sides unchanged, and the page history contains no version other
than those the commands above produced. Delete the page afterwards.

## License

Copyright (c) 2026 Sven Rosiers.

This project is licensed under the [Mozilla Public License 2.0](LICENSE.md).
