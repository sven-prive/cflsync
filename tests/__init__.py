# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Test package for cflsync.

Importing this package blocks outgoing socket connections. The automated suite
must run without a network connection and must never reach Confluence Cloud;
live acceptance against a disposable page is a manual operation. An accidental
live request therefore fails loudly instead of contacting a real site.

Only address resolution and connection establishment are blocked. Replacing the
socket class itself would break the standard library modules that subclass it.
"""

import socket


def _blocked_network(*arguments, **keywords):
    raise RuntimeError("the automated test suite must not open network connections")


setattr(socket, "getaddrinfo", _blocked_network)
setattr(socket.socket, "connect", _blocked_network)
setattr(socket.socket, "connect_ex", _blocked_network)

# vim: set ts=4 sw=4 et tw=132:
