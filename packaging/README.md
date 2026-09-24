# Windows release packaging

Windows release assets contain `cflsync.pyz`, a colocated-runtime launcher, and
a pinned CPython embeddable runtime described in `windows-python.json`. Linux
and macOS installations use `uv tool install` rather than a release archive.

Update that file with the CPython version, URLs, and upstream SHA-256 values,
then publish a new cflsync release. The runtime is part of every Windows asset,
so a CPython security update cannot be delivered independently.
