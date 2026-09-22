# cflsync

`cflsync` is a command-line tool for synchronizing Confluence Cloud pages with
local GitHub Flavored Markdown (GFM) page directories and their attachments.
The goal is local authoring of wiki pages in markdown format, using Confluence
as publishing platform rather than authoring environment.

## Documentation

- [Design](DESIGN.md): target behavior, storage layout, synchronization rules,
  and implementation constraints.
- [Mapping](MAPPING.md): `atlas_doc_format` to Pandoc AST mapping and opaque
  retention strategy.
- [Plan](PLAN.md): implementation roadmap.

## Prerequisites

- Python 3.11 or later.
- [uv](https://docs.astral.sh/uv/) for Python dependency management.
- Pandoc for the planned GFM and Pandoc-AST conversion boundary.

## Commands:

```text
cflsync auth [-p PROFILE] [--list | --delete]
cflsync init [-p PROFILE]
cflsync page create PARENT_PAGE_ID TITLE
cflsync page pull PAGE_REF
cflsync page push PAGE_REF
cflsync page status PAGE_REF
```

`PAGE_REF` may be a numeric Confluence page ID, an exact page title, a managed
local `page.md` file, or a managed page directory.

Use `cflsync --help` for top-level help, `cflsync page --help` for page-command
help, and `cflsync page COMMAND --help` for a command's arguments.

## Development

YAPF is the project formatter, set up to align closely with PEP-8 style. Apply
formatting before committing changes:

```console
uv run yapf --recursive --in-place cflsync.py tests
```

Check formatting without modifying files:

```console
uv run yapf --recursive --diff cflsync.py tests
```

## Testing

Run the complete test suite with:

```console
uv run python -m unittest discover -s tests -v
```

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE.md).
