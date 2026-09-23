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
from .convert import ADFToMarkdownConverter, PandocRunner
from .errors import SyncError
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
        page_create_parser.add_argument("parent_page_id", help="parent Confluence page ID")
        page_create_parser.add_argument("title", help="title for the new page")
        page_create_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.parent_page_id, args.title)

    def run(self, parent_page_id: str, title: str) -> int:
        raise SyncError("page create is not implemented")


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
            workarea = Workarea.find(Path.cwd())
            config = Config.find()
            profile = config.profiles.get(workarea.profile)
            if profile is None:
                raise SyncError(f"credential profile '{workarea.profile}' does not exist")

            api = APIClient(profile.hostname, profile.username, profile.apitoken)
            reference = PageRef.resolve(page_ref, workarea, api)
            page = api.get_page(reference.page_id)
            self._pull(workarea, page, PandocRunner(), force)
        except (OSError, UnicodeError) as error:
            raise SyncError(f"cannot pull page: {error}") from error

        return 0

    def _pull(self, workarea, page, pandoc, force=False):
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

            if not force and self._local_changed(source, previous, pandoc):
                raise SyncError(f"page '{page.id}' has local changes; pull conflicts")

            if not force and not self._remote_changed(page, attachments, previous):
                print(f"Page '{page.id}' is already in sync; nothing pulled. Use --force to regenerate local content.")
                return

        directory_name = workarea.page_directory_name(page.title)
        # Cached ownership also matters when a page directory is missing.
        for other_id, path in workarea.page_state_paths().items():
            other = PageState.load(path)
            if other_id != page.id and other.page.directory == directory_name:
                raise SyncError(f"page directory '{directory_name}' is assigned to page '{other_id}'")

        target = workarea.root_dir / directory_name
        if target.exists() and target != source:
            raise SyncError(f"page directory '{directory_name}' already exists")

        try:
            document = json.loads(page.body)
        except (TypeError, json.JSONDecodeError) as error:
            raise SyncError(f"page '{page.id}' has invalid ADF JSON") from error

        if not isinstance(document, dict):
            raise SyncError(f"page '{page.id}' ADF must be an object")

        markdown = ADFToMarkdownConverter(pandoc, media).convert(document, title=page.title)
        bodies = {}
        metadata = {}
        for attachment in attachments:
            body = attachment.download()
            bodies[attachment.filename] = body
            metadata[attachment.filename] = AttachmentMetadata(attachment.id, attachment.version, hashlib.sha256(body).hexdigest())

        state = PageState(
            PageMetadata(page.id, page.title, directory_name, page.version, self._page_hash(markdown, pandoc)), metadata)
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

    def _page_hash(self, markdown, pandoc):
        document = pandoc.gfm_to_pandoc(markdown)
        canonical = pandoc.pandoc_to_gfm(document)

        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _local_changed(self, directory, state, pandoc):
        markdown = (directory / "page.md").read_text(encoding="utf-8")
        if self._page_hash(markdown, pandoc) != state.page.content_hash:
            return True

        MediaResolver((name, attachment.id) for name, attachment in state.attachments.items())
        for name, attachment in state.attachments.items():
            path = directory / "_attachments" / name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != attachment.content_hash:
                return True

        return False

    def _remote_changed(self, page, attachments, state):
        if page.version != state.page.version or page.title != state.page.title:
            return True

        remote = {attachment.filename: (attachment.id, attachment.version) for attachment in attachments}
        cached = {name: (attachment.id, attachment.version) for name, attachment in state.attachments.items()}

        return remote != cached


class PagePushCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_push_parser = subparsers.add_parser("push", help="push a page to Confluence Cloud")
        page_push_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_push_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref)

    def run(self, page_ref: str) -> int:
        raise SyncError("page push is not implemented")


class PageStatusCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        page_status_parser = subparsers.add_parser("status", help="show a page's synchronization status")
        page_status_parser.add_argument("page_ref", help="page ID, title, page.md file, or page directory")
        page_status_parser.set_defaults(command=self)

    def __call__(self, args: Namespace) -> int:
        return self.run(args.page_ref)

    def run(self, page_ref: str) -> int:
        raise SyncError("page status is not implemented")


class PageCommand:

    def configure(self, subparsers: _SubParsersAction[ArgumentParser]) -> None:
        self.page_parser = subparsers.add_parser("page", help="page commands")
        self.page_parser.set_defaults(command=self)
        page_subparsers = self.page_parser.add_subparsers(title="page commands", metavar="command")
        PageCreateCommand().configure(page_subparsers)
        PagePullCommand().configure(page_subparsers)
        PagePushCommand().configure(page_subparsers)
        PageStatusCommand().configure(page_subparsers)

    def __call__(self, args: Namespace) -> int:
        self.page_parser.print_usage()
        return 0


def main(argv: Sequence[str]) -> int:
    parser = ArgumentParser(prog=argv[0])
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


def _print_usage(parser: ArgumentParser) -> int:
    parser.print_usage()
    return 0


# vim: set ts=4 sw=4 et tw=132:
