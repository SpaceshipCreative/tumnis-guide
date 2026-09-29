"""The server-path backend passes the shared storage contract on a temp dir, and no symlink
lets a path out of the location's root (P1-14, FR-15.7, SEC-5)."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tumnis.modules.knowledge.tests.contract.storage_contract import (
    StorageContract,
    chunks,
    read_all,
)

if TYPE_CHECKING:
    from pathlib import Path

    from tumnis.modules.knowledge.storage import StorageBackend


def _server_path(root: Path) -> StorageBackend:
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    return ServerPathStorage(root)


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
class TestServerPathStorage(StorageContract):
    """T-P1-14-02
    The shared storage suite passes on a temp dir holding the `.tumnis-root` marker.
    """

    impl = "real"
    adapter_name = "knowledge.server_path"

    @pytest.fixture
    def subject(self, tmp_location: Path) -> StorageBackend:
        return _server_path(tmp_location)


def _tree(path: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(path)): p.read_bytes()
        for p in sorted(path.rglob("*"))
        if p.is_file() and not p.is_symlink()
    }


@pytest.mark.contract
@pytest.mark.req("SEC-5")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
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
