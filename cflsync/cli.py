# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Command-line interface for cflsync."""

from __future__ import annotations

import sys
import hashlib
import json
import shutil
from argparse import ArgumentParser, Namespace, _SubParsersAction
from collections.abc import Sequence
from getpass import getpass
from pathlib import Path

from .api import APIClient
from .config import Config, Profile
from .convert import ADFToMarkdownConverter, MarkdownToADFConverter, PandocRunner
from .errors import SyncError
from .sync import PageInspector
from .workarea import AttachmentMetadata, MediaResolver, PageMetadata, PageRef, PageState, Workarea


class InitCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        init_parser = subparsers.add_parser("init", help="initialise a cflsync workarea in the current directory")
        init_parser.add_argument("-p", "--profile", default="default", help="use PROFILE instead of 'default'")
        init_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        Workarea.init(Path.cwd(), args.profile)
        return 0


class AuthCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        auth_parser = subparsers.add_parser("auth", help="store Confluence Cloud credentials")
        auth_parser.add_argument("-p", "--profile", default="default", help="store credentials under PROFILE instead of 'default'")
        auth_action = auth_parser.add_mutually_exclusive_group()
        auth_action.add_argument("-l", "--list", action="store_true", help="list available profiles")
        auth_action.add_argument("-d", "--delete", action="store_true", help="delete stored credentials")
        auth_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        config = Config.find()
        if args.list:
            for name in config.profiles:
                print(name)
            return 0

        if args.delete:
            if args.profile not in config.profiles:
                return 0
            if input(f"Delete profile '{args.profile}'? [y/N] ").lower() in ["y", "yes"]:
                del config.profiles[args.profile]
                config.save()
            return 0

        hostname = input("Confluence Cloud hostname: ")
        username = input("Confluence Cloud username: ")
        apitoken = getpass("Confluence Cloud API token: ")
        config.profiles[args.profile] = Profile(hostname, username, apitoken)
        config.save()
        return 0


class PageCreateCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_create_parser = subparsers.add_parser("create", help="create an empty Confluence Cloud child page")
        page_create_parser.add_argument("parent_page_ref", help="parent page ID, title, page.md file, or page directory")
        page_create_parser.add_argument("title", help="title for the new page")
        page_create_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.parent_page_ref, args.title)

    def run(self, parent_page_ref: str, title: str) -> int:
        _validate_page_title(title)

        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(parent_page_ref, workarea, api)
            parent = api.get_page(reference.page_id)
            if parent.space_id is None:
                raise SyncError(f"parent page '{reference.page_id}' reports no space")

            page = api.create_page(parent.space_id, parent.id, title)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot create page: {error}") from error

        try:
            return PagePullCommand().run(page.id)
        except SyncError as error:
            raise SyncError(f"created page '{page.id}' remotely but could not pull it: {error}") from error


class PagePullCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_pull_parser = subparsers.add_parser("pull", help="pull a page from Confluence Cloud")
        page_pull_parser.add_argument(
            "-f", "--force", action="store_true", help="prefer remote content, overwriting local changes to managed files")
        page_pull_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_pull_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref, force=args.force)

    def run(self, page_ref: str, force: bool = False) -> int:
        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(page_ref, workarea, api)
            page = api.get_page(reference.page_id)
            self._pull(workarea, page, PandocRunner(), api, force)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot pull page: {error}") from error

        return 0

    def _pull(self, workarea, page, pandoc, api, force=False):
        inspector = PageInspector(pandoc)
        attachments = page.attachments()
        MediaResolver((attachment.filename, attachment.id) for attachment in attachments)
        # ADF media nodes reference attachments by file ID, not by attachment ID.
        media = MediaResolver(
            (attachment.filename, attachment.file_id) for attachment in attachments if attachment.file_id is not None)
        cache_path = workarea.cache_path(page.id)
        previous = None
        source = None
        if cache_path.exists():
            previous = PageState.load(cache_path)
            source = workarea.page_directory(previous, must_exist=not force)
            if force and not source.exists():
                source = None

            if not force:
                changes = inspector.inspect(source, previous, page, attachments)
                if changes.locally:
                    raise SyncError(f"page '{page.id}' has local changes; pull conflicts")

                if not changes.remotely:
                    print(f"Page '{page.id}' is already in sync; nothing pulled. Use --force to regenerate local content.")
                    return

        directory_name = workarea.page_directory_name(page.title)
        # Cached ownership also matters when a page directory is missing.
        for other_id, path in workarea.page_state_paths().items():
            other = PageState.load(path)
            if other_id != page.id and other.page.directory.casefold() == directory_name.casefold():
                raise SyncError(f"page directory '{directory_name}' is assigned to page '{other_id}'")

        for existing in workarea.root_dir.iterdir():
            if existing.name.casefold() == directory_name.casefold() and existing != source:
                raise SyncError(f"page directory '{directory_name}' already exists")

        try:
            document = json.loads(page.body)
        except (TypeError, json.JSONDecodeError) as error:
            raise SyncError(f"page '{page.id}' has invalid ADF JSON") from error

        if not isinstance(document, dict):
            raise SyncError(f"page '{page.id}' ADF must be an object")

        markdown = ADFToMarkdownConverter(pandoc, media, api.get_user).convert(document, title=page.title)
        bodies = {}
        metadata = {}
        for attachment in attachments:
            body = attachment.download()
            bodies[attachment.filename] = body
            metadata[attachment.filename] = AttachmentMetadata(attachment.id, attachment.version, hashlib.sha256(body).hexdigest())

        state = PageState(
            PageMetadata(page.id, page.title, directory_name, page.version, inspector.content_hash(markdown)), metadata)
        managed = ()
        if previous is not None:
            managed = previous.attachments

        staging = workarea.stage_page(directory_name, markdown, bodies, source=source, managed_attachments=managed)
        try:
            with workarea.replace_page(staging, directory_name, source, set(managed) | set(bodies)):
                state.save(cache_path)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


class PagePushCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_push_parser = subparsers.add_parser("push", help="push a page to Confluence Cloud")
        page_push_parser.add_argument("-f", "--force", action="store_true", help="prefer local content, overwriting remote changes")
        page_push_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_push_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref, force=args.force)

    def run(self, page_ref: str, force: bool = False) -> int:
        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(page_ref, workarea, api)
            cache_path = workarea.cache_path(reference.page_id)
            if not cache_path.exists():
                raise SyncError(f"page '{reference.page_id}' is not managed in this workarea")

            state = PageState.load(cache_path)
            page = api.get_page(reference.page_id)
            self._push(workarea, page, state, cache_path, PandocRunner(), api, force)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot push page: {error}") from error

        return 0

    def _push(self, workarea, page, state, cache_path, pandoc, api, force=False):
        inspector = PageInspector(pandoc)
        directory = workarea.page_directory(state)
        attachments = page.attachments()
        changes = inspector.inspect(directory, state, page, attachments)
        if not force:
            if changes.remotely:
                raise SyncError(f"page '{page.id}' has remote changes; push conflicts")

            if not changes.locally:
                print(f"Page '{page.id}' is already in sync; nothing pushed. Use --force to upload local content.")
                return

        markdown = (directory / "page.md").read_text(encoding="utf-8")
        bodies = self._managed_attachments(directory, state, inspector, markdown)
        self._upload_attachments(page, state, bodies, attachments)
        # Re-read the manifest so new uploads contribute their server-assigned file IDs.
        remote = {attachment.filename: attachment for attachment in page.attachments()}
        document = self._convert(pandoc, markdown, page, bodies, remote, api)
        updated = page.update(json.dumps(document))
        self._delete_removed_attachments(state, bodies, remote)

        attachments = {}
        for name, body in bodies.items():
            attachments[name] = AttachmentMetadata(remote[name].id, remote[name].version, hashlib.sha256(body).hexdigest())

        PageState(
            PageMetadata(updated.id, updated.title, state.page.directory, updated.version, inspector.content_hash(markdown)),
            attachments).save(cache_path)

    def _managed_attachments(self, directory, state, inspector, markdown):
        """Return the bytes of every managed attachment still present locally."""
        names = set(state.attachments) | set(inspector.referenced_attachments(markdown))
        bodies = {}
        for name in sorted(names):
            path = directory / "_attachments" / name
            if path.is_file():
                bodies[name] = path.read_bytes()

        return bodies

    def _upload_attachments(self, page, state, bodies, attachments):
        remote = {attachment.filename: attachment for attachment in attachments}
        for name, body in bodies.items():
            existing = remote.get(name)
            if existing is None:
                page.create_attachment(name, body)
                continue

            cached = state.attachments.get(name)
            local_hash = hashlib.sha256(body).hexdigest()
            if cached is None or cached.content_hash != local_hash or (existing.id, existing.version) != (cached.id,
                                                                                                          cached.version):
                existing.update(body)

    def _delete_removed_attachments(self, state, bodies, remote):
        for name in state.attachments:
            if name not in bodies and name in remote:
                remote[name].delete()

    def _convert(self, pandoc, markdown, page, bodies, remote, api):
        # Attachments without a server-assigned file ID cannot be referenced from ADF.
        media = MediaResolver(
            (name, remote[name].file_id) for name in bodies if name in remote and remote[name].file_id is not None)
        return MarkdownToADFConverter(pandoc, media, f"contentId-{page.id}", api.find_user_by_name_and_email).convert(
            markdown, title=page.title)


class PageRenameCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_rename_parser = subparsers.add_parser("rename", help="rename a synchronized Confluence Cloud page")
        page_rename_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_rename_parser.add_argument("title", help="new page title")
        page_rename_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref, args.title)

    def run(self, page_ref: str, title: str) -> int:
        _validate_page_title(title)
        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(page_ref, workarea, api)
            cache_path = workarea.cache_path(reference.page_id)
            if not cache_path.exists():
                raise SyncError(f"page '{reference.page_id}' is not managed in this workarea")

            state = PageState.load(cache_path)
            page = api.get_page(reference.page_id)
            self._rename(workarea, page, state, cache_path, PandocRunner(), title)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot rename page: {error}") from error

        return 0

    def _rename(self, workarea, page, state, cache_path, pandoc, title):
        inspector = PageInspector(pandoc)
        directory = workarea.page_directory(state)
        attachments = page.attachments()
        changes = inspector.inspect(directory, state, page, attachments)
        if changes.locally or changes.remotely:
            raise SyncError(f"page '{page.id}' has local or remote changes; rename conflicts")

        if title == page.title:
            print(f"Page '{page.id}' is already named '{title}'; nothing renamed.")
            return

        if page.body is None:
            raise SyncError(f"page '{page.id}' has no ADF body")

        directory_name = workarea.page_directory_name(title)
        markdown = (directory / "page.md").read_text(encoding="utf-8")
        renamed_markdown = MarkdownToADFConverter(pandoc).retitle(markdown, state.page.title, title)
        content_hash = inspector.content_hash(renamed_markdown)
        target_state = PageState(PageMetadata(page.id, title, directory_name, page.version, content_hash), state.attachments)
        workarea.page_directory_target(target_state)
        staging = workarea.stage_page(directory_name, renamed_markdown, {}, source=directory)
        try:
            updated = page.update(page.body, title)
            if updated.title != title:
                raise SyncError(f"page '{page.id}' was renamed remotely to unexpected title '{updated.title}'")

            renamed_state = PageState(
                PageMetadata(updated.id, updated.title, directory_name, updated.version, content_hash), state.attachments)
            with workarea.replace_page(staging, directory_name, directory):
                renamed_state.save(cache_path)
        finally:
            if staging.exists():
                shutil.rmtree(staging)


class PageMoveCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_move_parser = subparsers.add_parser("move", help="move a synchronized Confluence Cloud page")
        page_move_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_move_parser.add_argument("new_parent_ref", help="page ID, title, page.md file, or page directory")
        page_move_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref, args.new_parent_ref)

    def run(self, page_ref: str, new_parent_ref: str) -> int:
        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(page_ref, workarea, api)
            cache_path = workarea.cache_path(reference.page_id)
            if not cache_path.exists():
                raise SyncError(f"page '{reference.page_id}' is not managed in this workarea")

            state = PageState.load(cache_path)
            page = api.get_page(reference.page_id)
            parent_reference = PageRef.resolve(new_parent_ref, workarea, api)
            parent = api.get_page(parent_reference.page_id)
            self._move(workarea, page, parent, state, cache_path, PandocRunner(), api)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot move page: {error}") from error

        return 0

    def _move(self, workarea, page, parent, state, cache_path, pandoc, api):
        inspector = PageInspector(pandoc)
        directory = workarea.page_directory(state)
        changes = inspector.inspect(directory, state, page, page.attachments())
        if changes.locally or changes.remotely:
            raise SyncError(f"page '{page.id}' has local or remote changes; move conflicts")

        if page.id == parent.id:
            raise SyncError("a page cannot be its own parent")
        if page.space_id is None:
            raise SyncError(f"page '{page.id}' reports no space")
        if parent.space_id is None:
            raise SyncError(f"new parent page '{parent.id}' reports no space")
        if page.space_id != parent.space_id:
            raise SyncError(f"new parent page '{parent.id}' is in a different space")
        if page.parent_id == parent.id:
            print(f"Page '{page.id}' is already a child of '{parent.id}'; nothing moved.")
            return

        if page.body is None:
            raise SyncError(f"page '{page.id}' has no ADF body")

        try:
            updated = page.update(page.body, parent_id=parent.id)
        except SyncError as error:
            raise SyncError(f"cannot move page '{page.id}' to parent '{parent.id}': {error}") from error
        if updated.parent_id != parent.id:
            raise SyncError(f"page '{page.id}' was moved remotely to unexpected parent '{updated.parent_id}'")
        if updated.title != state.page.title:
            raise SyncError(f"page '{page.id}' was moved remotely with unexpected title '{updated.title}'")

        moved_state = PageState(
            PageMetadata(updated.id, state.page.title, state.page.directory, updated.version, state.page.content_hash),
            state.attachments)
        try:
            moved_state.save(cache_path)
        except SyncError as error:
            raise SyncError(f"moved page '{page.id}' remotely but could not update local state: {error}") from error

class PageStatusCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_status_parser = subparsers.add_parser("status", help="show a page's synchronization status")
        page_status_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_status_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref)

    def run(self, page_ref: str) -> int:
        try:
            workarea, api = _open_workarea()
            reference = PageRef.resolve(page_ref, workarea, api)
            cache_path = workarea.cache_path(reference.page_id)
            if not cache_path.exists():
                raise SyncError(f"page '{reference.page_id}' is not managed in this workarea")

            state = PageState.load(cache_path)
            page = api.get_page(reference.page_id)
            # A missing page directory is a local change, not a lookup failure.
            directory = workarea.page_directory(state, must_exist=False)
            changes = PageInspector(PandocRunner()).inspect(directory, state, page, page.attachments())
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot report page status: {error}") from error

        print(f"Page '{state.page.id}' ({state.page.title})")
        print(f"  local:  {self._summary(changes.page_locally, 'page.md', changes.attachments_locally)}")
        print(f"  remote: {self._summary(changes.page_remotely, 'page', changes.attachments_remotely)}")

        return 0

    def _summary(self, page_changed, page_label, attachment_names):
        changed = [page_label] if page_changed else []
        changed.extend(f"_attachments/{name}" for name in attachment_names)
        if not changed:
            return "unchanged"

        return "changed: " + ", ".join(changed)


class PageCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        self.page_parser = subparsers.add_parser("page", help="page commands")
        self.page_parser.set_defaults(command=self)
        page_subparsers = self.page_parser.add_subparsers(title="page commands", metavar="command")
        PageCreateCommand().configure(page_subparsers)
        PagePullCommand().configure(page_subparsers)
        PagePushCommand().configure(page_subparsers)
        PageRenameCommand().configure(page_subparsers)
        PageMoveCommand().configure(page_subparsers)
        PageStatusCommand().configure(page_subparsers)

    def __call__(self, args: Namespace) -> int:
        self.page_parser.print_usage()
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one cflsync command, defaulting to this process's arguments."""
    if argv is None:
        argv = sys.argv

    parser = ArgumentParser(prog="cflsync")
    parser.set_defaults(command=lambda args: _print_usage(parser))
    subparsers = parser.add_subparsers(title="commands", metavar="command")
    AuthCommand().configure(subparsers)
    InitCommand().configure(subparsers)
    PageCommand().configure(subparsers)
    args = parser.parse_args(argv[1:])
    try:
        return args.command(args)
    except SyncError as error:
        print(f"{parser.prog}: {error}", file=sys.stderr)
        return 1


def _open_workarea():
    workarea = Workarea.find(Path.cwd())
    profile = Config.find().profiles.get(workarea.profile)
    if profile is None:
        raise SyncError(f"credential profile '{workarea.profile}' does not exist")

    return workarea, APIClient(profile.hostname, profile.username, profile.apitoken)


def _print_usage(parser: ArgumentParser) -> int:
    parser.print_usage()
    return 0


def _validate_page_title(title: str) -> None:
    if not title.strip() or title.strip() != title or "\n" in title or "\r" in title or "\t" in title:
        raise SyncError("page title must be non-empty single-line text without surrounding whitespace")


# vim: set ts=4 sw=4 et tw=132:
