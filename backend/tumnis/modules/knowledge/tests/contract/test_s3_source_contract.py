"""The S3 linked-source reader's contract (P3-13, FR-15.11): `S3SourceConnector` against the
`minio` container and `FakeS3Source` answer the same reads. Listing is by whole key under
a prefix, `stat` of a missing key is None, `read` of one is NotFound."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.modules.knowledge.adapters.port import S3SourceReader
from tumnis.modules.knowledge.storage import NotFound

if TYPE_CHECKING:
    from tests._services import S3Endpoint
    from tests.fixtures import Fakes

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

Put = Callable[[str, bytes], Awaitable[None]]


def _raw_s3(minio: S3Endpoint) -> Any:
    """An aioboto3 client on the container's root account (test setup only)."""
    import aioboto3  # type: ignore[import-untyped]  # noqa: PLC0415

    return aioboto3.Session().client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name=minio.region,
    )


async def _read(subject: S3SourceReader, key: str) -> bytes:
    return b"".join([chunk async for chunk in subject.read(key)])


async def _keys(subject: S3SourceReader, prefix: str) -> list[str]:
    keys: list[str] = []
    cursor: str | None = None
    while True:
        page = await subject.list(prefix, cursor)
        keys += [item.path for item in page.items]
        if page.next_cursor is None:
            return keys
        cursor = page.next_cursor


class S3SourceContract(AdapterContract[S3SourceReader]):
    port, adapter_name = S3SourceReader, "knowledge.s3_source"

    @pytest.fixture
    def put(self) -> Put:  # overridden per implementation
        raise NotImplementedError

    async def test_lists_whole_keys_under_a_prefix(self, subject: S3SourceReader, put: Put) -> None:
        await put("acme/brief.md", b"# Brief\n")
        await put("acme/notes/call.md", b"# Call\n")
        await put("acme-old/stale.md", b"# Stale\n")
        assert sorted(await _keys(subject, "acme/")) == ["acme/brief.md", "acme/notes/call.md"]

    async def test_stat_and_read(self, subject: S3SourceReader, put: Put) -> None:
        await put("acme/brief.md", b"# Brief\n\nThe Acme site.\n")
        stat = await subject.stat("acme/brief.md")
        assert stat is not None
        assert (stat.path, stat.size) == ("acme/brief.md", 24)
        assert stat.etag
        assert await _read(subject, "acme/brief.md") == b"# Brief\n\nThe Acme site.\n"

    async def test_missing_key(self, subject: S3SourceReader) -> None:
        assert await subject.stat("acme/ghost.md") is None
        with pytest.raises(NotFound):
            await _read(subject, "acme/ghost.md")

    async def test_health_is_ok(self, subject: S3SourceReader) -> None:
        assert (await subject.health()).status == "ok"


@pytest.mark.contract
@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
class TestFakeS3Source(S3SourceContract):
    """The in-memory bucket answers like the connector."""

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> S3SourceReader:
        reader: S3SourceReader = fakes[self.adapter_name]
        return reader

    @pytest.fixture
    def put(self, subject: S3SourceReader) -> Put:
        async def put(key: str, data: bytes) -> None:
            subject.put(key, data)  # type: ignore[attr-defined]

        return put


@pytest.mark.contract
@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
class TestS3SourceConnectorMinio(S3SourceContract):
    """The connector over P1-14's S3 client against the `minio` container (a fresh bucket
    per test)."""

    impl = "real"

    @pytest.fixture
    def bucket(self) -> str:
        return f"t-{uuid.uuid4().hex[:20]}"

    @pytest.fixture
    async def subject(self, minio: S3Endpoint, bucket: str) -> AsyncIterator[S3SourceReader]:
        from tumnis.modules.knowledge.adapters.s3 import S3Config  # noqa: PLC0415
        from tumnis.modules.knowledge.adapters.s3_source.connector import (  # noqa: PLC0415
            S3SourceConnector,
        )

        async with _raw_s3(minio) as s3:
            await s3.create_bucket(Bucket=bucket)
        config = S3Config(
            endpoint=minio.url,
            region=minio.region,
            access_key=minio.access_key,
            secret_key=minio.secret_key,
        )
        reader = S3SourceConnector(config, bucket=bucket)
        try:
            yield reader
        finally:
            await reader.aclose()

    @pytest.fixture
    def put(self, minio: S3Endpoint, bucket: str) -> Put:
        async def put(key: str, data: bytes) -> None:
            async with _raw_s3(minio) as s3:
                await s3.put_object(Bucket=bucket, Key=key, Body=data)

        return put
