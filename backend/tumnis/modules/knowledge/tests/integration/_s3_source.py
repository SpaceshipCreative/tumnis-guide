"""Shared setup for the S3 linked-source tests (P3-13): buckets and objects on the `minio`
container through its root account, MinIO users with read-only and read-write policies,
the endpoint as the source sees it (this host's LAN address: the SSRF guard refuses
loopback), and readers of the rows a sync wrote. Names are unique per call, since the
container is shared by every test of the session."""

from __future__ import annotations

import asyncio
import importlib
import socket
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    from tests._pg import DbUrls
    from tests._services import S3Endpoint
    from tests.fixtures import WorkspaceHandle

CREATE_ACTIONS = ("s3:GetObject", "s3:PutObject")
TERMINAL = frozenset({"ready", "quarantined", "failed"})


def unique(stem: str) -> str:
    return f"{stem}-{uuid.uuid4().hex[:10]}"


def lan_endpoint(minio: S3Endpoint) -> str:
    """The container's port on this host's own non-loopback address (a UDP connect sends
    nothing): a private address, which the self-hosted SSRF policy allows."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed anywhere
        address: str = probe.getsockname()[0]
    if address.startswith("127."):
        pytest.skip("no non-loopback address to reach the MinIO container on")
    return f"http://{address}:{urlsplit(minio.url).port}"


@asynccontextmanager
async def raw_s3(minio: S3Endpoint) -> AsyncIterator[Any]:
    """An aioboto3 client on the container's root account (test setup only)."""
    import aioboto3  # type: ignore[import-untyped]  # noqa: PLC0415

    async with aioboto3.Session().client(
        "s3",
        endpoint_url=minio.url,
        aws_access_key_id=minio.access_key,
        aws_secret_access_key=minio.secret_key,
        region_name=minio.region,
    ) as s3:
        yield s3


async def new_bucket(minio: S3Endpoint, *, versioned: bool = False) -> str:
    bucket = unique("p313")
    async with raw_s3(minio) as s3:
        await s3.create_bucket(Bucket=bucket)
        if versioned:
            await s3.put_bucket_versioning(
                Bucket=bucket, VersioningConfiguration={"Status": "Enabled"}
            )
    return bucket


async def put(minio: S3Endpoint, bucket: str, key: str, body: bytes) -> None:
    async with raw_s3(minio) as s3:
        await s3.put_object(Bucket=bucket, Key=key, Body=body)


async def remove(minio: S3Endpoint, bucket: str, key: str) -> None:
    """A plain delete: on a versioned bucket it leaves a delete marker."""
    async with raw_s3(minio) as s3:
        await s3.delete_object(Bucket=bucket, Key=key)


async def version_count(minio: S3Endpoint, bucket: str, key: str) -> int:
    async with raw_s3(minio) as s3:
        found = await s3.list_object_versions(Bucket=bucket, Prefix=key)
    return len([v for v in found.get("Versions", []) if v["Key"] == key])


@dataclass(frozen=True)
class MinioUser:
    access_key: str
    secret_key: str


def minio_user(minio: S3Endpoint, policy: dict[str, Any]) -> MinioUser:
    """A MinIO user holding `policy` (made through the admin API on the root account)."""
    from minio.credentials import StaticProvider  # type: ignore[attr-defined]  # noqa: PLC0415
    from minio.minioadmin import MinioAdmin  # noqa: PLC0415

    parts = urlsplit(minio.url)
    admin = MinioAdmin(
        endpoint=parts.netloc,
        credentials=StaticProvider(minio.access_key, minio.secret_key),
        secure=parts.scheme == "https",
    )
    user = MinioUser(unique("user"), unique("test-secret"))
    policy_name = unique("policy")
    admin.policy_add(policy_name, policy=policy)
    admin.user_add(user.access_key, user.secret_key)
    admin.policy_set(policy_name, user=user.access_key)
    return user


def read_only_policy(bucket: str, prefix: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/{prefix}*"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
                "Condition": {"StringLike": {"s3:prefix": [f"{prefix}*"]}},
            },
        ],
    }


def writable_policy(bucket: str) -> dict[str, Any]:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [*CREATE_ACTIONS, "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }
        ],
    }


def source_in(  # the form's fields
    minio: S3Endpoint,
    bucket: str,
    prefixes: dict[str, UUID | None],
    *,
    provider: str = "other",
    user: MinioUser | None = None,
    trusted: bool = False,
    endpoint: str | None = None,
) -> Any:
    """The S3 source form: root keys unless `user` is given, the LAN endpoint unless
    `endpoint` is."""
    knowledge: Any = importlib.import_module("tumnis.modules.knowledge.api")

    return knowledge.S3SourceIn(
        provider=provider,
        endpoint=endpoint or lan_endpoint(minio),
        region=minio.region,
        bucket=bucket,
        access_key=user.access_key if user else minio.access_key,
        secret_key=user.secret_key if user else minio.secret_key,
        path_style=True,
        trusted=trusted,
        prefixes=[
            knowledge.S3PrefixMap(prefix=prefix, project_id=project_id)
            for prefix, project_id in prefixes.items()
        ],
    )


async def create_source(ws: WorkspaceHandle, body: Any) -> Any:
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415

    knowledge: Any = importlib.import_module("tumnis.modules.knowledge.api")

    async with tenant_session(ws.ctx) as s:
        return await knowledge.create_s3_source(ws.ctx, s, body, net=NetPolicy(mode="self-hosted"))


async def source_documents(
    ws: WorkspaceHandle, connection_id: UUID, *, trashed: bool = False
) -> dict[str, dict[str, Any]]:
    """The source's documents by object key (live ones, or trashed ones)."""
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.models import Document  # noqa: PLC0415

    t = Document.__table__
    deleted = t.c.deleted_at.is_not(None) if trashed else t.c.deleted_at.is_(None)
    async with tenant_session(ws.ctx) as s:
        rows = (
            (await s.execute(select(t).where(t.c.connection_id == connection_id, deleted)))
            .mappings()
            .all()
        )
    return {row["external_id"]: dict(row) for row in rows}


async def version_numbers(ws: WorkspaceHandle, document_id: UUID) -> list[int]:
    from sqlalchemy import select  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge.models import DocumentVersion  # noqa: PLC0415

    t = DocumentVersion.__table__
    async with tenant_session(ws.ctx) as s:
        found: Any = await s.scalars(
            select(t.c.version_no).where(t.c.document_id == document_id).order_by(t.c.version_no)
        )
        return list(found)


async def wait_terminal(db: DbUrls, document_id: object, timeout_s: float = 60) -> str:
    """The document's status once the pipeline is done with it (ready, quarantined,
    failed)."""
    import psycopg  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    deadline = time.monotonic() + timeout_s
    while True:
        with psycopg.connect(db.libpq(OWNER)) as conn:
            row = conn.execute("SELECT status FROM documents WHERE id = %s", (document_id,))
            status = str(row.fetchone()[0])  # type: ignore[index]
        if status in TERMINAL:
            return status
        if time.monotonic() > deadline:
            raise TimeoutError(f"{document_id} still {status}")
        await asyncio.sleep(0.1)


@dataclass
class ExtractLog:
    """An extraction hook that records each request instead of enqueueing it."""

    requests: list[tuple[UUID, UUID]]

    async def __call__(self, workspace_id: UUID, version_id: UUID) -> None:
        self.requests.append((workspace_id, version_id))
