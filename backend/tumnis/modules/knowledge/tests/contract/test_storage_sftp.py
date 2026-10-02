"""The SFTP backend passes the shared storage contract against the `sftp_server` container
with a pinned host key, and never follows a symlink planted on the server (P3-14, FR-15.7,
FR-15.12)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tumnis.modules.knowledge.tests._sftp import make_root, raw_sftp, sftp_storage
from tumnis.modules.knowledge.tests.contract.storage_contract import (
    HOSTILE_EXAMPLES,
    StorageContract,
    chunks,
    read_all,
)

if TYPE_CHECKING:
    from tests._services import SftpEndpoint

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P3-14")
class TestSftpStorage(StorageContract):
    """T-P3-14-01
    The shared storage suite (stat, list, read, write, move, delete, a stale if_match
    refused) passes on the SFTP container: key login, the host key pinned at fixture start,
    a fresh root per test.
    """

    impl = "real"
    adapter_name = "knowledge.sftp"

    @pytest.fixture
    async def subject(self, sftp_server: SftpEndpoint) -> AsyncIterator[Any]:
        backend = sftp_storage(sftp_server, await make_root(sftp_server))
        try:
            yield backend
        finally:
            await backend.aclose()


_HOSTILE = st.one_of(
    st.sampled_from(HOSTILE_EXAMPLES),
    st.builds(
        "/".join,
        st.lists(
            st.sampled_from(["..", ".", "", "~", "a", "\uff0e\uff0e", "a\u2215b", "C:", "\x00",
                             "\u202e", "a\\b", "\u2024"]),
            min_size=1, max_size=4,
        ),
    ),
)  # fmt: skip


@pytest.mark.contract
@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
async def test_hostile_paths_refused(sftp_server: SftpEndpoint) -> None:  # noqa: PLR0915
    """T-P3-14-03
    P1-14's hostile paths (and Hypothesis's mixes of their pieces) are refused before
    anything reaches the server; a symlink planted on the server to `/etc` (and one to a
    folder inside the root) is never followed: stat, read, write, move, delete and list
    refuse it, a listing leaves it out, and nothing lands where it points.
    """
    from tumnis.modules.knowledge.rules import safe_rel_path  # noqa: PLC0415
    from tumnis.modules.knowledge.storage import PathRejected  # noqa: PLC0415

    root = await make_root(sftp_server)
    backend = sftp_storage(sftp_server, root)
    try:
        await backend.write("ok.txt", chunks(b"ok"), if_match=None)
        await backend.write("inner/kept.txt", chunks(b"kept"), if_match=None)

        @settings(
            max_examples=60,
            deadline=None,
            suppress_health_check=[HealthCheck.function_scoped_fixture],
        )
        @given(bad=_HOSTILE)
        def refused(bad: str) -> None:
            try:
                safe_rel_path(bad)
            except PathRejected:
                pass
            else:
                return  # a safe path after all: not this test's business
            with pytest.raises(PathRejected):
                _run(backend.stat(bad))
            with pytest.raises(PathRejected):
                _run(read_all(backend, bad))
            with pytest.raises(PathRejected):
                _run(backend.write(bad, chunks(b"x"), if_match=None))
            with pytest.raises(PathRejected):
                _run(backend.delete(bad))

        await _in_thread(refused)

        sftp_server.plant_symlink(f"{root}/etc-link", "/etc")
        sftp_server.plant_symlink(f"{root}/passwd-link", "/etc/passwd")
        sftp_server.plant_symlink(f"{root}/inner-link", f"/{root}/inner")
        for path in (
            "etc-link/passwd",
            "etc-link/new.txt",
            "passwd-link",
            "inner-link/kept.txt",
            "inner-link/new.txt",
        ):
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
            await backend.list("etc-link/", None)
        listed = sorted(s.path for s in (await backend.list("", None)).items)
        assert listed == ["inner/kept.txt", "ok.txt"]
        async with raw_sftp(sftp_server) as sftp:
            assert await sftp.exists(f"{root}/ok.txt")
            assert not await sftp.exists(f"{root}/inner/new.txt")
            assert not await sftp.exists(f"{root}/moved.txt")
        assert await read_all(backend, "inner/kept.txt") == b"kept"
    finally:
        await backend.aclose()


def _run(coro: Any) -> Any:
    """Run `coro` on this worker thread's own loop (Hypothesis drives a plain function)."""
    import asyncio  # noqa: PLC0415

    return asyncio.run(coro)


async def _in_thread(fn: Any) -> None:
    import asyncio  # noqa: PLC0415

    await asyncio.to_thread(fn)
