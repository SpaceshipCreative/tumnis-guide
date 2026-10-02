"""MinIO bucket notifications for an S3 linked source (P3-13, FR-15.11).

MinIO's webhook target does not sign its body; it sends the `auth_token` configured on
the target in the `Authorization` header (as `Bearer <token>` when the token is one word;
minio/minio `internal/event/target/webhook.go`). So the token Tumnis generated for the
connection stands in for the signature the PRD asks for (deviation flagged for Scott in
the plan). A notification is never trusted: it only queues a re-check (a HEAD) of the
key it names, in the connection's own bucket."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.modules.knowledge.tests.integration._s3_source import (
    create_source,
    new_bucket,
    put,
    source_documents,
    source_in,
)

if TYPE_CHECKING:
    import httpx
    from dbos import DBOS, DBOSClient

    from tests._pg import DbUrls
    from tests._services import S3Endpoint
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

RECHECK = "knowledge_s3_source_recheck"


def _event(bucket: str, *keys: str) -> bytes:
    """A MinIO notification as its webhook target sends it: one record per object, the
    object key query-escaped as in S3 event records."""
    records = [
        {
            "eventVersion": "2.0",
            "eventSource": "minio:s3",
            "eventName": "s3:ObjectCreated:Put",
            "s3": {
                "s3SchemaVersion": "1.0",
                "bucket": {"name": bucket, "arn": f"arn:aws:s3:::{bucket}"},
                "object": {"key": quote(key, safe=""), "size": 10, "eTag": "0" * 32},
            },
        }
        for key in keys
    ]
    return json.dumps(
        {"EventName": "s3:ObjectCreated:Put", "Key": f"{bucket}/{keys[0]}", "Records": records}
    ).encode()


async def _settled(dbos_client: DBOSClient, count: int, timeout_s: float = 30) -> list[Any]:
    """The re-check workflows once `count` of them finished."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        found = await asyncio.to_thread(dbos_client.list_workflows, name=RECHECK)
        done = [w for w in found if w.status not in {"PENDING", "ENQUEUED"}]
        if len(done) >= count:
            return done
        if loop.time() > deadline:
            raise TimeoutError(f"{RECHECK}: {[w.status for w in found]}")
        await asyncio.sleep(0.05)


def _count(db: DbUrls, sql: str, *params: object) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_notification_requires_token(
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    client: httpx.AsyncClient,
    minio: S3Endpoint,
) -> None:
    """T-P3-13-07
    `POST /v1/webhooks/minio/{connection_id}` with no `Authorization`, a wrong bearer
    token, or the right token for another connection answers 401 `invalid_token` and
    queues nothing. With the connection's token (the one shown once at create) it answers
    202 and queues a re-check of the named key only: that object becomes a Document of
    the mapped project, while another new object in the same prefix waits for the next
    listing. The token stands in for a signature (deviation flagged for Scott).
    """
    env = extract_env
    bucket = await new_bucket(minio)
    await put(minio, bucket, "acme/one.md", b"# One\n")
    await put(minio, bucket, "acme/two.md", b"# Two\n")
    source = await create_source(env.ws, source_in(minio, bucket, {"acme/": env.project_id}))
    other = await create_source(env.ws, source_in(minio, bucket, {"lab/": None}))
    assert source.webhook_token
    assert other.webhook_token
    assert source.webhook_token != other.webhook_token
    path = f"/v1/webhooks/minio/{source.id}"
    body = _event(bucket, "acme/one.md")

    for headers in (
        {},
        {"Authorization": "Bearer not-the-token"},
        {"Authorization": f"Bearer {other.webhook_token}"},
        {"Authorization": source.webhook_token},  # no scheme: MinIO always sends one
    ):
        refused = await client.post(path, content=body, headers=headers)
        assert refused.status_code == 401, refused.text
        assert refused.json()["code"] == "invalid_token"
    assert await asyncio.to_thread(dbos_client.list_workflows, name=RECHECK) == []

    accepted = await client.post(
        path, content=body, headers={"Authorization": f"Bearer {source.webhook_token}"}
    )
    assert accepted.status_code == 202, accepted.text
    (run,) = await _settled(dbos_client, 1)
    assert run.status == "SUCCESS"

    docs = await source_documents(env.ws, source.id)
    assert set(docs) == {"acme/one.md"}
    assert docs["acme/one.md"]["project_id"] == env.project_id


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_forged_body_cannot_create_document(  # noqa: PLR0917
    db: DbUrls,
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    client: httpx.AsyncClient,
    minio: S3Endpoint,
) -> None:
    """T-P3-13-08
    A notification with the right token whose body names a key that does not exist in
    the connection's bucket, a key outside every mapped prefix, and a key in another
    bucket only causes a HEAD of each in the connection's own bucket: no Document, no
    version, no `folder_files` row, and no extraction.
    """
    env = extract_env
    bucket = await new_bucket(minio)
    elsewhere = await new_bucket(minio)
    await put(minio, bucket, "other/real.md", b"# Real but not mapped\n")
    await put(minio, elsewhere, "acme/planted.md", b"# Planted\n")
    source = await create_source(env.ws, source_in(minio, bucket, {"acme/": env.project_id}))
    headers = {"Authorization": f"Bearer {source.webhook_token}"}
    path = f"/v1/webhooks/minio/{source.id}"

    first = await client.post(
        path, content=_event(bucket, "acme/ghost.md", "other/real.md"), headers=headers
    )
    second = await client.post(path, content=_event(elsewhere, "acme/planted.md"), headers=headers)
    assert (first.status_code, second.status_code) == (202, 202)
    runs = await _settled(dbos_client, 3)
    assert {r.status for r in runs} == {"SUCCESS"}

    assert await source_documents(env.ws, source.id) == {}
    assert _count(db, "SELECT count(*) FROM documents WHERE connection_id = %s", source.id) == 0
    assert _count(db, "SELECT count(*) FROM folder_files WHERE connection_id = %s", source.id) == 0
    assert list(env.dirs.spool.iterdir()) == []
