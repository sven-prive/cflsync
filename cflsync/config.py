# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""User-wide cflsync credential profiles."""

from __future__ import annotations

import json
from pathlib import Path

from platformdirs import user_config_dir


class Profile:
    """Credentials for one named Confluence Cloud profile."""

    def __init__(self, hostname: str, username: str, apitoken: str):
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

        with path.open(encoding="utf-8") as config_file:
            items = json.load(config_file)

        profiles = {}
        for name, values in items.items():
            profiles[name] = Profile.from_json(values)

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

    def save(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path.parent.chmod(0o700)
        with self.path.open("w", encoding="utf-8") as config_file:
            json.dump(self.to_json(), config_file, indent=2)
            config_file.write("\n")
        self.path.chmod(0o600)

    def to_json(self) -> dict[str, dict[str, str]]:
        return {name: profile.to_json() for name, profile in self.profiles.items()}


# vim: set ts=4 sw=4 et tw=132:
