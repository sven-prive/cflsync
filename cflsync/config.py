# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""User-wide cflsync credential profiles."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from platformdirs import user_config_dir

from .errors import SyncError


class ConfigError(SyncError):
    """Raised when credential configuration cannot be read or written safely."""


class Profile:
    """Credentials for one named Confluence Cloud profile."""

    def __init__(self, hostname: str, username: str, apitoken: str):
        if not all(isinstance(value, str) and value for value in [hostname, username, apitoken]):
            raise ConfigError("credential profile fields must be non-empty strings")

        self.hostname = hostname
        self.username = username
        self.apitoken = apitoken

    @classmethod
    def from_json(cls, values: dict[str, str]) -> "Profile":
        return cls(hostname=values["hostname"], username=values["username"], apitoken=values["apitoken"])

    def to_json(self) -> dict[str, str]:
        return {"hostname": self.hostname, "username": self.username, "apitoken": self.apitoken}


class Config:
    """Credential profiles stored in the platform configuration directory."""

    def __init__(self, path: Path, profiles: dict[str, Profile] | None = None):
        self.path = path
        self._profiles = profiles or {}

    @classmethod
    def find(cls):
        path = Path(user_config_dir("cflsync")) / "config.json"
        if not path.exists():
            return cls(path)

        try:
            with path.open(encoding="utf-8") as config_file:
                items = json.load(config_file)
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ConfigError(f"cannot read credential configuration: {error}") from error
        if not isinstance(items, dict):
            raise ConfigError("credential configuration must be an object")

        profiles = {}
        for name, values in items.items():
            if not isinstance(name, str) or not isinstance(values, dict):
                raise ConfigError("credential configuration has an invalid profile")
            try:
                profiles[name] = Profile.from_json(values)
            except (ConfigError, KeyError, TypeError) as error:
                raise ConfigError(f"credential profile '{name}' is invalid") from error

        return cls(path, profiles)

    @property
    def profiles(self) -> dict[str, Profile]:
        return self._profiles

    @profiles.setter
    def profiles(self, value):
        raise AttributeError("can't assign to read-only property 'profiles'")

    @profiles.deleter
    def profiles(self):
        raise AttributeError("can't delete read-only property 'profiles'")

    def save(self) -> None:
        temporary_path: Path | None = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self.path.parent.chmod(0o700)
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent, prefix=".config.", suffix=".tmp",
                                    delete=False) as temporary_file:
                temporary_path = Path(temporary_file.name)
                temporary_path.chmod(0o600)
                json.dump(self.to_json(), temporary_file, indent=2)
                temporary_file.write("\n")
                temporary_file.flush()
                os.fsync(temporary_file.fileno())

            os.replace(temporary_path, self.path)
        except OSError as error:
            raise ConfigError(f"cannot write credential configuration: {error}") from error
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink()
                except OSError:
                    pass

    def to_json(self) -> dict[str, dict[str, str]]:
        return {name: profile.to_json() for name, profile in self.profiles.items()}


# vim: set ts=4 sw=4 et tw=132:
