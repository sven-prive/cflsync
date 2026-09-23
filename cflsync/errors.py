# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Shared cflsync exception base classes."""


class SyncError(Exception):
    """Base class for failures reported by cflsync."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


# vim: set ts=4 sw=4 et tw=132:
