"""A server path configured as a share (`kind="share"`) passes the shared storage contract
(P3-14, FR-15.7, FR-15.12): the mounted SMB or NFS folder of a user, written as a network
filesystem (no hard links assumed) and only while its `.tumnis-root` marker is there."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.contract.storage_contract import StorageContract, chunks

if TYPE_CHECKING:
    from pathlib import Path


def _share(root: Path) -> Any:
    from tumnis.modules.knowledge.adapters.server_path import ServerPathStorage  # noqa: PLC0415

    return ServerPathStorage(root, kind="share")


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P3-14")
@pytest.mark.xfail(strict=True, reason="spec:P3-14")
class TestShareStorage(StorageContract):
    """T-P3-14-02
    The shared storage suite passes on a temp dir configured as a share: the backend
    reports `kind == "share"`, treats the folder as a network filesystem, and refuses to
    write once the marker is gone.
    """

    impl = "real"
    adapter_name = "knowledge.server_path"

    @pytest.fixture
    def subject(self, tmp_location: Path) -> Any:
        backend = _share(tmp_location)
        assert backend.kind == "share"
        assert backend.network_fs is True
        return backend

    async def test_share_refuses_writes_without_its_marker(
        self, subject: Any, tmp_location: Path
    ) -> None:
        from tumnis.modules.knowledge.storage import LocationOffline  # noqa: PLC0415

        (tmp_location / ".tumnis-root").unlink()
        assert (await subject.health()).reason == "marker_missing"
        with pytest.raises(LocationOffline):
            await subject.write("a.txt", chunks(b"a"), if_match=None)
        assert sorted(p.name for p in tmp_location.iterdir()) == []  # noqa: ASYNC240
