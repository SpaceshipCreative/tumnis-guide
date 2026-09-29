"""The server-path backend passes the shared storage contract on a temp dir, and no symlink
lets a path out of the location's root (P1-14, FR-15.7, SEC-5)."""

from __future__ import annotations

import contextlib
import errno
import os
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.contract.storage_contract import (
    StorageContract,
    chunks,
    read_all,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tumnis.modules.knowledge.storage import StorageBackend


def _server_path(root: Path) -> StorageBackend:
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    return ServerPathStorage(root)


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
class TestServerPathStorage(StorageContract):
    """T-P1-14-02
    The shared storage suite passes on a temp dir holding the `.tumnis-root` marker.
    """

    impl = "real"
    adapter_name = "knowledge.server_path"

    @pytest.fixture
    def subject(self, tmp_location: Path) -> StorageBackend:
        return _server_path(tmp_location)


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
class TestServerPathStorageWithoutHardLinks(StorageContract):
    """The shared storage suite on a share that refuses link() (SMB, some FUSE): creates go
    through the exclusive open and moves through the checked rename."""

    impl = "real"
    adapter_name = "knowledge.server_path"

    @pytest.fixture
    def subject(self, tmp_location: Path, monkeypatch: pytest.MonkeyPatch) -> StorageBackend:
        from tumnis.modules.knowledge.adapters import server_path  # noqa: PLC0415

        def no_link(*_args: object, **_kwargs: object) -> None:
            raise OSError(errno.EPERM, "link() not supported")

        monkeypatch.setattr(os, "link", no_link)  # the adapter calls os.link
        return server_path.ServerPathStorage(tmp_location, network_fs=True)


def _tree(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_symlink_escape_refused(tmp_location: Path, tmp_path: Path) -> None:
    """T-P1-14-07
    A symlink inside the root pointing outside it (to a file, and to a directory) is
    refused for read, write, stat, move and delete; the outside tree is unchanged and a
    listing does not follow the link.
    """
    from tumnis.modules.knowledge.storage import PathRejected  # noqa: PLC0415

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    os.symlink(outside / "secret.txt", tmp_location / "link.txt")
    os.symlink(outside, tmp_location / "linkdir")
    (tmp_location / "inner").mkdir()
    os.symlink(tmp_location / "inner", tmp_location / "inner-link")  # even inside the root
    before = _tree(outside)
    backend = _server_path(tmp_location)
    await backend.write("ok.txt", chunks(b"ok"), if_match=None)

    for path in ("link.txt", "linkdir/secret.txt", "linkdir/new.txt", "inner-link/a.txt"):
        with pytest.raises(PathRejected):
            await backend.stat(path)
        with pytest.raises(PathRejected):
            await read_all(backend, path)
        with pytest.raises(PathRejected):
            await backend.write(path, chunks(b"x"), if_match=None)
        with pytest.raises(PathRejected):
            await backend.write(path, chunks(b"x"), if_match="0" * 64)
        with pytest.raises(PathRejected):
            await backend.move(path, "moved.txt")
        with pytest.raises(PathRejected):
            await backend.move("ok.txt", path)
        with pytest.raises(PathRejected):
            await backend.delete(path)

    with pytest.raises(PathRejected):
        await backend.list("linkdir/", None)
    listed = [s.path for s in (await backend.list("", None)).items]
    assert listed == ["ok.txt"]
    assert _tree(outside) == before
    assert (tmp_location / "link.txt").is_symlink()
    assert (tmp_location / "linkdir").is_symlink()
    assert await read_all(backend, "ok.txt") == b"ok"


class _SwapParent:
    """Swaps the folder `root/a` for a symlink to `outside` once, right after the adapter's
    path check (`_resolve`): the time-of-check/time-of-use race of PR #52's review."""

    def __init__(self, root: Path, outside: Path) -> None:
        self.root, self.outside = root, outside

    def swap(self) -> None:
        folder = self.root / "a"
        if folder.is_dir() and not folder.is_symlink():
            os.rename(folder, self.root / "a-moved")
            os.symlink(self.outside, folder)

    def after_resolve(self, backend: object, monkeypatch: pytest.MonkeyPatch) -> None:
        resolve = backend._resolve  # type: ignore[attr-defined]

        def checked_then_swapped(rel: str) -> object:
            found = resolve(rel)
            self.swap()
            return found

        monkeypatch.setattr(backend, "_resolve", checked_then_swapped)


def _race_setup(tmp_location: Path, tmp_path: Path) -> tuple[Path, _SwapParent]:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"secret")
    (tmp_location / "a").mkdir()
    (tmp_location / "a" / "secret.txt").write_bytes(b"inside")
    return outside, _SwapParent(tmp_location, outside)


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_read_refuses_a_parent_swapped_for_a_symlink_after_the_check(
    tmp_location: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tumnis.modules.knowledge.storage import PathRejected  # noqa: PLC0415

    _outside, race = _race_setup(tmp_location, tmp_path)
    backend = _server_path(tmp_location)
    race.after_resolve(backend, monkeypatch)

    with pytest.raises(PathRejected):
        await read_all(backend, "a/secret.txt")


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_delete_refuses_a_parent_swapped_for_a_symlink_after_the_check(
    tmp_location: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tumnis.modules.knowledge.storage import PathRejected  # noqa: PLC0415

    outside, race = _race_setup(tmp_location, tmp_path)
    backend = _server_path(tmp_location)
    race.after_resolve(backend, monkeypatch)

    with pytest.raises(PathRejected):
        await backend.delete("a/secret.txt")
    assert (outside / "secret.txt").read_bytes() == b"secret"


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
async def test_pr52_stat_refuses_a_parent_swapped_for_a_symlink_after_the_check(
    tmp_location: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tumnis.modules.knowledge.storage import PathRejected  # noqa: PLC0415

    _outside, race = _race_setup(tmp_location, tmp_path)
    backend = _server_path(tmp_location)
    race.after_resolve(backend, monkeypatch)

    with pytest.raises(PathRejected):
        await backend.stat("a/secret.txt")


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
@pytest.mark.parametrize(
    "network_fs",
    [False, True],
)
async def test_pr52_write_never_lands_outside_when_a_parent_is_swapped_mid_write(
    tmp_location: Path, tmp_path: Path, network_fs: bool
) -> None:
    from tumnis.modules.knowledge.adapters.server_path import (  # noqa: PLC0415
        ServerPathStorage,
    )

    outside, race = _race_setup(tmp_location, tmp_path)
    before = _tree(outside)
    backend = ServerPathStorage(tmp_location, network_fs=network_fs)

    async def body() -> AsyncIterator[bytes]:
        yield b"first half "
        race.swap()  # after the folders were checked and the temp file opened
        yield b"second half"

    with contextlib.suppress(Exception):
        await backend.write("a/new.txt", body(), if_match=None)
    assert _tree(outside) == before


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
async def test_pr52_failed_copy_on_a_share_without_links_leaves_no_partial_file(
    tmp_location: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On a share without link() a create copies the temp file into an exclusive new name;
    when the copy fails partway (ENOSPC, EIO) the new name goes too, so a retry creates it
    instead of meeting a truncated file."""
    from tumnis.core.adapters.errors import AdapterError  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters import server_path  # noqa: PLC0415

    def no_link(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.EPERM, "link() not supported")

    write_all = server_path._write_all

    def full_disk_outside_temp(fd: int, chunk: bytes) -> None:
        if (
            not os.readlink(f"/proc/self/fd/{fd}")
            .rpartition("/")[2]
            .startswith(server_path.TMP_PREFIX)
        ):
            raise OSError(errno.ENOSPC, "No space left on device")
        write_all(fd, chunk)

    monkeypatch.setattr(os, "link", no_link)
    monkeypatch.setattr(server_path, "_write_all", full_disk_outside_temp)
    backend = server_path.ServerPathStorage(tmp_location, network_fs=True)

    with pytest.raises(AdapterError):
        await backend.write("note.md", chunks(b"complete"), if_match=None)
    assert not (tmp_location / "note.md").exists()

    monkeypatch.setattr(server_path, "_write_all", write_all)
    await backend.write("note.md", chunks(b"complete"), if_match=None)
    assert await read_all(backend, "note.md") == b"complete"
