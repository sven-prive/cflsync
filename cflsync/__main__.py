# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""Run the cflsync command-line interface."""

import sys

from .cli import main

raise SystemExit(main(sys.argv))

# vim: set ts=4 sw=4 et tw=132:
