# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Command-line interface for cflsync."""

from __future__ import annotations

from argparse import ArgumentParser
from getpass import getpass
from pathlib import Path

from . import Workarea
from .config import Config, Profile


class InitCommand:

    def configure(self, subparsers):
        init_parser = subparsers.add_parser("init", help="initialise a cflsync workarea in the current directory")
        init_parser.add_argument("-p", "--profile", default="default", help="use PROFILE instead of 'default'")
        init_parser.set_defaults(command=self)

    def __call__(self, args):
        Workarea.init(Path.cwd(), args.profile)
        return 0


class AuthCommand:

    def configure(self, subparsers):
        auth_parser = subparsers.add_parser("auth", help="store Confluence Cloud credentials")
        auth_parser.add_argument("-p", "--profile", default="default", help="store credentials under PROFILE instead of 'default'")
        auth_action = auth_parser.add_mutually_exclusive_group()
        auth_action.add_argument("-l", "--list", action="store_true", help="list available profiles")
        auth_action.add_argument("-d", "--delete", action="store_true", help="delete stored credentials")
        auth_parser.set_defaults(command=self)

    def __call__(self, args):
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


class PagePullCommand:

    def configure(self, subparsers):
        page_pull_parser = subparsers.add_parser("pull", help="pull a page from Confluence Cloud")
        page_pull_parser.add_argument("page_ref", help="page id or title of the page to pull")
        page_pull_parser.set_defaults(command=self)

    def __call__(self, args):
        pass


class PageCommand:

    def configure(self, subparsers):
        self.page_parser = subparsers.add_parser("page", help="page commands")
        self.page_parser.set_defaults(command=self)
        page_subparsers = self.page_parser.add_subparsers(title="page commands", metavar="command")
        PagePullCommand().configure(page_subparsers)

    def __call__(self, args):
        self.page_parser.print_usage()


def main(argv):
    parser = ArgumentParser(prog=argv[0])
    parser.set_defaults(command=lambda args: parser.print_usage())
    subparsers = parser.add_subparsers(title="commands", metavar="command")
    AuthCommand().configure(subparsers)
    InitCommand().configure(subparsers)
    PageCommand().configure(subparsers)
    args = parser.parse_args(argv[1:])
    return args.command(args)


# vim: set ts=4 sw=4 et tw=132:
