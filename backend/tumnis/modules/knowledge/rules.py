"""knowledge pure rules: no I/O, `now` and `tz` passed in.

Storage paths (P1-14, SEC-5): `safe_rel_path` is the one gate every storage path passes
before any backend touches it. The storage errors live here, not in `storage.py`, because
rules may import only their own module's rules (`storage.py` re-exports them).
"""

# The names are the plan's shared contract (P1-14 interfaces), not "...Error".
# ruff: noqa: N818

from typing import Final


class StorageError(Exception):
    """Base of every storage answer (`storage.py` holds the rest)."""


class PathRejected(StorageError):
    """The path could escape the location's root, or is not one Tumnis writes."""


LOOKALIKE_SEPARATORS: Final = frozenset("\u2215\u2044\uff0f\u29f8\ufe68\uff3c\\")
LOOKALIKE_DOTS: Final = frozenset("\u2024\u2025\u2026\uff0e\ufe52")
MAX_PATH_BYTES, MAX_SEGMENT_BYTES = 1024, 255  # plan defaults


def safe_rel_path(p: str) -> str:
    """NFC-normalize, then refuse: empty; leading '/' or '~'; a drive letter ('C:'); NUL or
    control characters; any look-alike separator or dot; '.', '..' or empty segments
    ('a//b'); a trailing '/'; a path whose NFKC form differs from its NFC form in any '.' or
    '/'; over the byte limits. Returns the normalized relative path."""
    raise NotImplementedError


def is_network_fs(fstype: str) -> bool:
    """'cifs', 'smb3', 'nfs', 'nfs4', 'fuse.sshfs' -> True."""
    raise NotImplementedError


def etag_equal(a: str | None, b: str | None) -> bool:
    """Two etags name the same content: quotes and case ignored, None equals only None."""
    raise NotImplementedError
