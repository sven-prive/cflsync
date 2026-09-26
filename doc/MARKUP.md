# Writing Confluence pages

`content.md` uses GitHub Flavored Markdown (GFM). This guide covers the Markdown
that cflsync converts to editable Confluence content. Pulling a page can also
produce preserved Confluence content that should not be edited as Markdown;
see [Macros](#macros).

## A page at a glance

```markdown
# Release checklist

## Before release

Complete the [release procedure](https://example.invalid/release).

- [ ] Review changes
- [x] Update the changelog

> [!WARNING]
> Publish only from the protected branch.

```sh
git tag v1.2.3
git push origin v1.2.3
```

| Check | Owner |
| --- | --- |
| Release notes | Documentation |

![Architecture diagram](_attachments/architecture.png)
```

The first `#` heading is the page title. It is added by `page pull`, must stay
the first and only level-one heading, and cannot rename the remote page. Use
`page rename PAGE_REF TITLE` to rename a page. Use `##` through `######` for
headings in the page body.

## Supported Markdown

| Content | Markdown |
| --- | --- |
| Paragraphs and hard line breaks | Ordinary text; end a line with two spaces for a hard break. |
| Emphasis | `*italic*`, `**bold**`, `~~strikethrough~~`, `` `code` ``, `<u>underline</u>`, `<sub>subscript</sub>`, and `<sup>superscript</sup>` |
| Links | `[label](https://example.com)` |
| Headings | `## Heading` through `###### Heading` |
| Blockquotes | `> Quoted text` |
| Panels | `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, or `> [!CAUTION]`, followed by quoted panel content |
| Lists | `- item`, `1. item`, and task items such as `- [ ] todo` or `- [x] done` |
| Code blocks | Fenced blocks such as ```` ```python ````; the language is retained by Confluence |
| Horizontal rules | `---` |
| Tables | Pipe tables with a header row, for example `| Name | Value |` |
| Attachments | `![](_attachments/image.png)` for images and `[](_attachments/file.pdf)` for downloadable files |

Nest list items with indentation. Images or files in `_attachments/` are
uploaded and maintained with the page. Links outside `_attachments/` remain
ordinary links; external images are supported as external media.

## Tables

Use pipe tables for ordinary tables:

```markdown
| Service | State |
| --- | --- |
| API | Ready |
| Worker | Maintenance |
```

Pulled tables that require merged cells or multiple blocks in a cell may be
written as HTML tables. These are accepted on push, but table layout, widths,
cell colours, alignment, and similar presentation settings are not retained.

## Macros

### Supported macros

Pulled dates and status lozenges are represented by special HTML spans, which
cflsync converts back to their Confluence forms on push. Mentions whose users
have no visible email address also use a special HTML span. They can be edited,
but their required attributes must remain intact:

- Change the text inside a status span and, if needed, its
  `background-color` to `gray`, `purple`, `blue`, `red`, `yellow`, or `green`.
- Change a date span's `YYYY-MM-DD[Region/City]` text. The symbolic time zone
  makes the date deterministic across contributors. A date without `[Region/City]`
  uses the local machine time zone on push.
- A mention span needs its non-empty `cfl-id`; preserve its other metadata
  unless the corresponding Confluence account values are known.

These are cflsync-specific HTML forms rather than ordinary Markdown syntax.
For example, a pulled date is written as:

```html
<span cfl-type="date">2026-04-01[Europe/Brussels]</span>
```

Date spans with `cfl-timestamp` preserve that
timestamp unchanged.

### Creating mentions

On push, a plain email link can create a Confluence user mention:

```markdown
[Example User](mailto:example.user@example.com)
```

cflsync searches for `Example User` and emits a mention only when exactly one
accessible result has the given email address. If no result or multiple results
match, the link remains an ordinary email link. Pull writes this form for a
mention whose user has a visible email address; otherwise it writes the
`cfl-type="mention"` HTML span.

### Other macros

Confluence macro content without an ordinary Markdown representation is written
as a fenced `atlas_doc_format` block containing its original JSON. Leave these
blocks unchanged: cflsync restores them on the next push. They are not a
Markdown authoring format.

## Round-trip limitations

Some content is converted but loses presentation detail: panel colours and
icons, image size and layout, and advanced table formatting. Check the remote
page after pushing changes to pages that use these features.
