"""`StorageContract`: the one suite every storage backend passes (P1-14, FR-15.7, SEC-5).

P0-09's contract base: each backend's `Test*` class sets `impl` and `adapter_name` and
overrides the `subject` fixture; the cases live here. `HOSTILE_EXAMPLES` is shared with
the sync engine's tests (P1-15).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from hashlib import sha256

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.knowledge.storage import (
    NotFound,
    PathRejected,
    PreconditionFailed,
    StorageBackend,
)

HOSTILE_EXAMPLES = [
    "../x",
    "a/../../x",
    "/etc/passwd",
    "~/x",
    "C:\\x",
    "a\\b",
    "a/\u2215b",
    "\uff0e\uff0e/x",
    "a//b",
    "a/",
    "a\x00b",
    "a/\u202eb",
    "",
]


async def chunks(data: bytes, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    for start in range(0, len(data), size):
        yield data[start : start + size]


async def read_all(backend: StorageBackend, path: str) -> bytes:
    return b"".join([c async for c in backend.read(path)])


class StorageContract(AdapterContract[StorageBackend]):
    port = StorageBackend

    async def test_stat_missing_is_none(self, subject: StorageBackend) -> None:
        assert await subject.stat("missing.txt") is None

    async def test_create_then_stat_and_read(self, subject: StorageBackend) -> None:
        st = await subject.write("a/b.txt", chunks(b"hello"), if_match=None)
        assert (st.size, st.path) == (5, "a/b.txt")
        assert st.etag
        again = await subject.stat("a/b.txt")
        assert again is not None
        assert (again.etag, again.size) == (st.etag, 5)
        assert await read_all(subject, "a/b.txt") == b"hello"

    async def test_large_file_streams(self, subject: StorageBackend) -> None:
        data = os.urandom(3 * 1024 * 1024 + 7)
        await subject.write("big.bin", chunks(data, 64 * 1024), if_match=None)
        assert sha256(await read_all(subject, "big.bin")).digest() == sha256(data).digest()

    async def test_create_refuses_existing(self, subject: StorageBackend) -> None:
        first = await subject.write("x.txt", chunks(b"1"), if_match=None)
        with pytest.raises(PreconditionFailed) as e:
            await subject.write("x.txt", chunks(b"2"), if_match=None)
        assert e.value.current is not None
        assert e.value.current.etag == first.etag
        assert await read_all(subject, "x.txt") == b"1"

    async def test_replace_needs_matching_etag(self, subject: StorageBackend) -> None:
        v1 = await subject.write("x.txt", chunks(b"1"), if_match=None)
        v2 = await subject.write("x.txt", chunks(b"2"), if_match=v1.etag)
        with pytest.raises(PreconditionFailed) as e:
            await subject.write("x.txt", chunks(b"3"), if_match=v1.etag)  # stale
        assert e.value.current is not None
        assert e.value.current.etag == v2.etag
        current = await subject.stat("x.txt")
        assert current is not None
        assert current.etag == v2.etag
        assert await read_all(subject, "x.txt") == b"2"

    async def test_replace_of_missing_path_fails(self, subject: StorageBackend) -> None:
        with pytest.raises(PreconditionFailed) as e:
            await subject.write("gone.txt", chunks(b"1"), if_match="0" * 32)
        assert e.value.current is None
        assert await subject.stat("gone.txt") is None

    async def test_list_pages_every_file_once(self, subject: StorageBackend) -> None:
        names = {f"p/{i:04}.txt" for i in range(1050)}
        for n in names:
            await subject.write(n, chunks(b"."), if_match=None)
        await subject.write("q/other.txt", chunks(b"."), if_match=None)
        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            page = await subject.list("p/", cursor)
            pages += 1
            seen += [s.path for s in page.items]
            if not (cursor := page.next_cursor):
                break
        assert sorted(seen) == sorted(names)
        assert pages >= 2

    async def test_move_keeps_bytes_and_refuses_existing_destination(
        self, subject: StorageBackend
    ) -> None:
        await subject.write("m/src.txt", chunks(b"moved"), if_match=None)
        await subject.write("m/taken.txt", chunks(b"keep"), if_match=None)
        await subject.move("m/src.txt", "n/dst.txt")
        assert await subject.stat("m/src.txt") is None
        assert await read_all(subject, "n/dst.txt") == b"moved"
        with pytest.raises(PreconditionFailed):
            await subject.move("n/dst.txt", "m/taken.txt")
        assert await read_all(subject, "m/taken.txt") == b"keep"
        assert await read_all(subject, "n/dst.txt") == b"moved"
        with pytest.raises(NotFound):
            await subject.move("m/nothing.txt", "m/else.txt")

    async def test_delete_then_missing_and_delete_missing_is_noop(
        self, subject: StorageBackend
    ) -> None:
        await subject.write("d.txt", chunks(b"x"), if_match=None)
        await subject.delete("d.txt")
        assert await subject.stat("d.txt") is None
        with pytest.raises(NotFound):
            await read_all(subject, "d.txt")
        await subject.delete("d.txt")  # no-op
        await subject.delete("never/was.txt")

    @pytest.mark.parametrize("bad", HOSTILE_EXAMPLES)
    async def test_every_method_refuses_hostile_path(
        self, subject: StorageBackend, bad: str
    ) -> None:
        await subject.write("ok.txt", chunks(b"ok"), if_match=None)
        with pytest.raises(PathRejected):
            await subject.stat(bad)
        with pytest.raises(PathRejected):
            await read_all(subject, bad)
        with pytest.raises(PathRejected):
            await subject.write(bad, chunks(b"x"), if_match=None)
        with pytest.raises(PathRejected):
            await subject.move("ok.txt", bad)
        with pytest.raises(PathRejected):
            await subject.move(bad, "elsewhere.txt")
        with pytest.raises(PathRejected):
            await subject.delete(bad)
        # A list prefix may be empty (the whole location) or end in one '/' (a folder).
        if bad not in ("", "a/"):
            with pytest.raises(PathRejected):
                await subject.list(bad, None)
        assert await read_all(subject, "ok.txt") == b"ok"

    async def test_health_ok(self, subject: StorageBackend) -> None:
        assert (await subject.health()).status == "ok"
