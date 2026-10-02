"""The Obsidian vault reader port (P3-12, FR-15.10): two readers behind one interface, a
read-only mounted folder (`FolderReader`) and a Git clone (`GitReader`), and the fake.

A listing gives each file's vault-relative path, size, mtime and content hash (`etag`, the
sha256 hex of the bytes). `skip` is asked about every folder and file before it is opened,
so excluded folders (`.obsidian/`, `.trash/`, templates, the user's) are never read; the
readers prune `.git/` themselves. Nothing here ever writes to the vault.
"""

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from tumnis.modules.knowledge.storage import FileStat

__all__ = ["FileStat", "Skip", "VaultReader", "never_skip"]

Skip = Callable[[str], bool]


def never_skip(_path: str) -> bool:
    return False


@runtime_checkable
class VaultReader(Protocol):
    async def list_files(self, skip: Skip = never_skip) -> list[FileStat]:
        """Every regular file in the vault that `skip` keeps (symlinks are neither listed
        nor followed), sorted by path."""
        ...

    async def read(self, path: str) -> bytes:
        """The file's bytes; `storage.NotFound` when it is gone, `storage.TooLarge` past
        50 MiB, `PathRejected` for a path that leaves the vault."""
        ...

    async def refresh(self) -> None:
        """Bring the vault up to date before a listing: git fetch and reset; a mounted
        folder is always current (no-op)."""
        ...
