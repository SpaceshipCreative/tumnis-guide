"""The S3 backend passes the shared storage contract against the `minio` container, and its
HEAD-check fallback catches a concurrent change where conditional puts are off (P1-14,
FR-15.7)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.knowledge.tests.contract.storage_contract import (
    StorageContract,
    chunks,
    read_all,
)

if TYPE_CHECKING:
    from tests._services import S3Endpoint
    from tumnis.modules.knowledge.adapters.s3 import S3Storage

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _raw_client(minio: S3Endpoint) -> Any:
    import aioboto3  # type: ignore[import-untyped]  # noqa: PLC0415

    return aioboto3.Session().client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name=minio.region,
    )


async def make_bucket(minio: S3Endpoint) -> str:
    bucket = f"t-{uuid.uuid4().hex[:20]}"
    async with _raw_client(minio) as s3:
        await s3.create_bucket(Bucket=bucket)
    return bucket


def s3_storage(
    minio: S3Endpoint, bucket: str, *, prefix: str = "", conditional_put: bool = True
) -> S3Storage:
    from tumnis.modules.knowledge.adapters.s3 import S3Config, S3Storage  # noqa: PLC0415

    config = S3Config(
        endpoint=minio.url,
        region=minio.region,
        access_key=minio.access_key,
        secret_key=minio.secret_key,
        path_style=True,
    )
    return S3Storage(config, bucket=bucket, prefix=prefix, conditional_put=conditional_put)


@pytest.mark.contract
@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
class TestS3StorageMinio(StorageContract):
    """T-P1-14-03
    The shared storage suite passes against the `minio` container (a fresh bucket per
    test, keys under a location prefix).
    """

    impl = "real"
    adapter_name = "knowledge.s3"

    @pytest.fixture
    async def subject(self, minio: S3Endpoint) -> AsyncIterator[S3Storage]:
        backend = s3_storage(minio, await make_bucket(minio), prefix="tumnis/")
        try:
            yield backend
        finally:
            await backend.aclose()


@pytest.mark.req("FR-15.7")
@pytest.mark.wp("P1-14")
@pytest.mark.xfail(strict=True, reason="spec:P1-14")
async def test_head_check_fallback_catches_concurrent_change(minio: S3Endpoint) -> None:
    """T-P1-14-11
    With `conditional_put` forced off, another client changes the object after the caller
    read its etag: the caller's write raises PreconditionFailed carrying the new etag, and
    the other client's bytes stay.
    """
    from tumnis.modules.knowledge.storage import PreconditionFailed  # noqa: PLC0415

    bucket = await make_bucket(minio)
    backend = s3_storage(minio, bucket, conditional_put=False)
    try:
        first = await backend.write("x.txt", chunks(b"mine"), if_match=None)
        async with _raw_client(minio) as other:
            await other.put_object(Bucket=bucket, Key="x.txt", Body=b"theirs")
            head = await other.head_object(Bucket=bucket, Key="x.txt")
        theirs = head["ETag"].strip('"')
        assert theirs != first.etag
        with pytest.raises(PreconditionFailed) as e:
            await backend.write("x.txt", chunks(b"mine again"), if_match=first.etag)
        assert e.value.current is not None
        assert e.value.current.etag == theirs
        with pytest.raises(PreconditionFailed):
            await backend.write("x.txt", chunks(b"create"), if_match=None)
        assert await read_all(backend, "x.txt") == b"theirs"
    finally:
        await backend.aclose()
