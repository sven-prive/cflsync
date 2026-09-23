# cflsync

`cflsync` is a command-line tool for synchronizing Confluence Cloud pages with
local GitHub Flavored Markdown (GFM) page directories and their attachments.
The goal is local authoring of wiki pages in markdown format, using Confluence
as publishing platform rather than authoring environment.

## Documentation

- [Design](DESIGN.md): implementation constraints, scope, and architectural
  decisions.
- [Specification](SPEC.md): command behavior, local storage, and
  synchronization rules.
- [Mapping](MAPPING.md): `atlas_doc_format` to Pandoc AST mapping and opaque
  retention strategy.
- [Plan](PLAN.md): implementation roadmap.

## Prerequisites

- Python 3.11 or later.
- [uv](https://docs.astral.sh/uv/) for Python dependency management.
- Pandoc for the GFM and Pandoc-AST conversion boundary.

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

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE.md).
