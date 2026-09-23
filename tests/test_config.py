# Copyright (c) 2026 Sven Rosiers
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Credential configuration persistence tests."""

import json
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cflsync import Config, ConfigError, Profile


class TestConfigSave(unittest.TestCase):

    def test_writes_private_configuration_atomically(self) -> None:
        with TemporaryDirectory(prefix="cflsync-config-") as temporary_dir:
            path = Path(temporary_dir) / "config/config.json"
            config = Config(path, {"default": Profile("example.atlassian.net", "user", "token")})

            config.save()

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), config.to_json())
            if os.name != "nt":
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_failed_replacement_preserves_previous_configuration(self) -> None:
        with TemporaryDirectory(prefix="cflsync-config-") as temporary_dir:
            path = Path(temporary_dir) / "config.json"
            previous = Config(path, {"default": Profile("example.atlassian.net", "user", "token")})
            previous.save()
            before = path.read_bytes()
            changed = Config(path, {"default": Profile("example.atlassian.net", "other", "token")})

            with patch("cflsync.config.os.replace", side_effect=OSError("injected replacement failure")):
                with self.assertRaisesRegex(ConfigError, "cannot write"):
                    changed.save()

            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob(".config.*.tmp")), [])

    def test_rejects_a_malformed_credential_file(self) -> None:
        with TemporaryDirectory(prefix="cflsync-config-") as temporary_dir:
            path = Path(temporary_dir) / "config.json"
            path.write_text('{"default": {"hostname": "example.atlassian.net"}}', encoding="utf-8")

            with patch("cflsync.config.user_config_dir", return_value=temporary_dir):
                with self.assertRaisesRegex(ConfigError, "profile 'default' is invalid"):
                    Config.find()


# vim: set ts=4 sw=4 et tw=132:
