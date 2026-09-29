"""`FakeStorage`: the in-memory tree behind every storage adapter in fakes mode (P1-14)."""

from collections.abc import AsyncIterator

from tumnis.modules.knowledge.storage import FileStat, Health, Page


class FakeStorage:
    async def stat(self, path: str) -> FileStat | None:
        raise NotImplementedError

    async def list(self, prefix: str, cursor: str | None) -> Page[FileStat]:
        raise NotImplementedError

    def read(self, path: str) -> AsyncIterator[bytes]:
        raise NotImplementedError

    async def write(self, path: str, data: AsyncIterator[bytes], if_match: str | None) -> FileStat:
        raise NotImplementedError

    async def move(self, src: str, dst: str) -> None:
        raise NotImplementedError

    async def delete(self, path: str) -> None:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError
