# cflsync

`cflsync` is a command-line tool for synchronizing Confluence Cloud pages with
local GitHub Flavored Markdown (GFM) page directories and their attachments.
The goal is local authoring of wiki pages in markdown format, using Confluence
as publishing platform rather than authoring environment.

## Installation

### Windows

Install from the personal [scoop](https://scoop.sh) bucket:

```console
scoop bucket add sven https://github.com/sven-prive/scoop
scoop install cflsync
```

The package bundles CPython and installs Pandoc as a Scoop dependency, so no
separate Python installation is required.

### Other operating systems (Linux, MacOS...)

Install using [uv](https://docs.astral.sh/uv/).

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
cflsync page create PARENT_PAGE_REF TITLE
cflsync page pull [-f | --force] PAGE_REF
cflsync page push [-f | --force] PAGE_REF
cflsync page rename PAGE_REF TITLE
cflsync page move PAGE_REF NEW_PARENT_REF
cflsync page remove [-f | --force] PAGE_REF
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
rename pages. Use `page rename PAGE_REF TITLE` to change the remote title,
generated heading, and title-derived local directory as one explicit operation.

Use `page move PAGE_REF NEW_PARENT_REF` to change a synchronized page's remote
parent. The new parent must exist remotely in the same Confluence space; the
current loose-page workarea does not move the local page directory.

Use `page remove PAGE_REF` to delete a managed local page and its remote page.
It asks for confirmation unless `--force` is supplied. If the remote page no
longer exists, it removes only the local page and cache state.

Use `cflsync --help` for top-level help, `cflsync page --help` for page-command
help, and `cflsync page COMMAND --help` for a command's arguments.

## Prerequisites

- Python 3.11 or later.
- [Pandoc](https://pandoc.org/) for markup conversion.

## Documentation

- [Writing pages](doc/MARKUP.md): supported Markdown for Confluence pages.

## Development

### Implementation documentation

- [Design](doc/DESIGN.md): implementation constraints, scope, and architectural
  decisions.
- [Specification](doc/SPEC.md): command behavior, local storage, and
  synchronization rules.
- [Mapping](doc/MAPPING.md): `atlas_doc_format` to Pandoc AST mapping and
  opaque retention strategy.

### Development tooling

Required tooling:

- [uv](https://docs.astral.sh/uv/) manages the development environment and runs project commands.
- [YAPF](https://github.com/google/yapf/) formats Python code through uv's `dev` dependency group.
- [Zuban](https://docs.zubanls.com/) performs static type checking.

YAPF is configured to align closely with PEP-8 style. Apply formatting before
committing changes:

```console
uv run --group dev python -m yapf --recursive --in-place cflsync tests packaging/scripts
```

Check formatting without modifying files:

```console
uv run --group dev python -m yapf --recursive --diff cflsync tests packaging/scripts
```

Check the application and test sources with Zuban:

```console
zuban check cflsync tests
```

Build the self-contained zipapp distribution artifact:

```console
uv run python packaging/scripts/build_zipapp.py
```

### Testing

Run the complete test suite with:

```console
uv run python -m unittest discover -s tests -v
```

The automated suite uses only local filesystem fixtures and recorded HTTP
transports. Importing the `tests` package blocks address resolution and socket
connections, so an accidental live request fails instead of reaching a real
site.

## License

Copyright (c) 2026 Sven Rosiers.

This project is licensed under the [Mozilla Public License 2.0](LICENSE.md).
