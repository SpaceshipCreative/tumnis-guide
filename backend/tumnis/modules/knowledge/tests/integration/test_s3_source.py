"""S3 buckets as a linked source (P3-13, FR-15.11, SEC-5, SEC-10): a key that can write is
refused where MinIO lets Tumnis check it, a versioned bucket indexes only its latest
version, bucket prefixes map to projects and their files are processed like uploads, and
the endpoint passes the SSRF guard before anything is sent."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER
from tumnis.core.net import NetPolicy
from tumnis.modules.knowledge.tests._samples import eicar
from tumnis.modules.knowledge.tests.integration._s3_source import (
    ExtractLog,
    create_source,
    minio_user,
    new_bucket,
    put,
    read_only_policy,
    remove,
    source_documents,
    source_in,
    version_count,
    version_numbers,
    wait_terminal,
    writable_policy,
)

if TYPE_CHECKING:
    from uuid import UUID

    from dbos import DBOS

    from tests._pg import DbUrls
    from tests._services import S3Endpoint
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock
    from tumnis.modules.knowledge.tests.integration.conftest import ExtractEnv

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SELF_HOSTED = NetPolicy(mode="self-hosted")


def _count(db: DbUrls, sql: str, *params: object) -> int:
    with psycopg.connect(db.libpq(OWNER)) as conn:
        row = conn.execute(sql, params).fetchone()
    assert row is not None
    return int(row[0])


async def _project(ws: WorkspaceHandle, clock: FixedClock, name: str) -> UUID:
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        project = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name=name), now=clock.now()
        )
        await knowledge.assign_project_folder(s, project.id)
    return project.id


async def _sync(ws: WorkspaceHandle, connection_id: UUID, extract: ExtractLog | None) -> None:
    s3_sync = importlib.import_module("tumnis.modules.knowledge.s3_sync")
    await s3_sync.sync_source(ws.ctx, connection_id, net=SELF_HOSTED, extract=extract)


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_writable_minio_key_refused(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint
) -> None:
    """T-P3-13-03
    With the `minio` container and two users, a key whose policy allows PutObject on the
    bucket is refused at connect (422 `key_not_read_only`, nothing saved, no connection
    made); a key whose policy allows only GetObject and a prefix listing is accepted, its
    capabilities checked through MinIO's account info (read-only, scoped to the prefix).
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415

    bucket = await new_bucket(minio)
    rw_user = minio_user(minio, writable_policy(bucket))
    ro_user = minio_user(minio, read_only_policy(bucket, "acme/"))

    with pytest.raises(ProblemError) as refused:
        await create_source(
            knowledge_ws, source_in(minio, bucket, {"acme/": None}, provider="minio", user=rw_user)
        )
    assert (refused.value.status, refused.value.code) == (422, "key_not_read_only")
    assert _count(db, "SELECT count(*) FROM s3_sources") == 0
    assert _count(db, "SELECT count(*) FROM connections WHERE provider = 's3'") == 0

    created = await create_source(
        knowledge_ws, source_in(minio, bucket, {"acme/": None}, provider="minio", user=ro_user)
    )
    caps = created.capabilities
    assert (caps.checked, caps.source) == (True, "minio_account_info")
    assert (caps.can_read, caps.can_write, caps.can_delete) == (True, False, False)
    assert caps.bucket_scoped is True
    assert created.warning is None
    assert _count(db, "SELECT count(*) FROM s3_sources") == 1


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_only_latest_version_indexed(
    knowledge_ws: WorkspaceHandle, minio: S3Endpoint, clock: FixedClock
) -> None:
    """T-P3-13-05
    A versioned MinIO bucket holds three versions of one key, written one at a time with a
    sync after each: there is one Document, with one version row per sync that saw a new
    version (1, 2, 3), and one extraction per version; a fourth sync with nothing changed
    adds nothing. Deleting the key leaves a delete marker: the next sync moves the
    Document to the trash.
    """
    ws = knowledge_ws
    project_id = await _project(ws, clock, "Acme")
    bucket = await new_bucket(minio, versioned=True)
    key = "acme/brief.md"
    created = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    log = ExtractLog([])

    for n in (1, 2, 3):
        await put(minio, bucket, key, f"# Brief\n\nVersion {n}.\n".encode())
        await _sync(ws, created.id, log)
    assert await version_count(minio, bucket, key) == 3
    await _sync(ws, created.id, log)

    docs = await source_documents(ws, created.id)
    assert set(docs) == {key}
    doc = docs[key]
    assert (doc["project_id"], doc["status"]) == (project_id, "pending_scan")
    assert await version_numbers(ws, doc["id"]) == [1, 2, 3]
    assert len(log.requests) == 3

    await remove(minio, bucket, key)
    await _sync(ws, created.id, log)
    assert await source_documents(ws, created.id) == {}
    assert set(await source_documents(ws, created.id, trashed=True)) == {key}
    assert len(log.requests) == 3


@pytest.mark.req("FR-15.11", "SEC-10")
@pytest.mark.wp("P3-13")
async def test_prefix_maps_to_project_and_processes_like_upload(
    db: DbUrls,
    extract_env: ExtractEnv,
    dbos: type[DBOS],
    minio: S3Endpoint,
    clock: FixedClock,
) -> None:
    """T-P3-13-06
    Two prefixes map to two projects: `acme/` to Acme, `lab/` to Lab; a key under no
    mapped prefix is never synced. Each file goes through the upload pipeline (scan,
    extraction): the clean files end `ready`, the EICAR object ends `quarantined`. Files
    from an untrusted connection are untrusted and tainted; a second connection marked
    trusted brings its file in trusted and untainted.
    """
    env = extract_env
    ws = env.ws
    acme, lab = env.project_id, await _project(ws, clock, "Lab")
    bucket = await new_bucket(minio)
    await put(minio, bucket, "acme/brief.md", b"# Brief\n\nThe Acme site.\n")
    await put(minio, bucket, "lab/plan.md", b"# Plan\n\nLab work.\n")
    await put(minio, bucket, "acme/dropped.txt", eicar())
    await put(minio, bucket, "other/skip.md", b"# Not mapped\n")
    await put(minio, bucket, "shared/rates.md", b"# Rates\n\nDay rate.\n")

    untrusted = await create_source(ws, source_in(minio, bucket, {"acme/": acme, "lab/": lab}))
    trusted = await create_source(ws, source_in(minio, bucket, {"shared/": acme}, trusted=True))
    await _sync(ws, untrusted.id, None)
    await _sync(ws, trusted.id, None)

    docs = await source_documents(ws, untrusted.id)
    assert set(docs) == {"acme/brief.md", "lab/plan.md", "acme/dropped.txt"}
    assert {k: d["project_id"] for k, d in docs.items()} == {
        "acme/brief.md": acme,
        "lab/plan.md": lab,
        "acme/dropped.txt": acme,
    }
    statuses = {k: await wait_terminal(db, d["id"]) for k, d in docs.items()}
    assert statuses == {
        "acme/brief.md": "ready",
        "lab/plan.md": "ready",
        "acme/dropped.txt": "quarantined",
    }
    for doc in docs.values():
        assert (doc["trust"], doc["tainted"]) == ("untrusted", True)

    shared = await source_documents(ws, trusted.id)
    assert set(shared) == {"shared/rates.md"}
    rates = shared["shared/rates.md"]
    assert await wait_terminal(db, rates["id"]) == "ready"
    assert (rates["project_id"], rates["trust"], rates["tainted"]) == (acme, "trusted", False)


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P3-13")
async def test_ssrf_guard_on_endpoint(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint
) -> None:
    """T-P3-13-09
    An endpoint at the cloud metadata address `http://169.254.169.254` is refused with 422
    `ssrf_blocked` before any request is sent (no capability check, no listing), whatever
    the provider; nothing is saved.
    """
    from tumnis.core.errors import ProblemError  # noqa: PLC0415

    for provider in ("other", "minio"):
        body = source_in(
            minio,
            "tumnis-docs",
            {"acme/": None},
            provider=provider,
            endpoint="http://169.254.169.254",
        )
        with pytest.raises(ProblemError) as refused:
            await create_source(knowledge_ws, body)
        assert (refused.value.status, refused.value.code) == (422, "ssrf_blocked")
    assert _count(db, "SELECT count(*) FROM s3_sources") == 0
    assert _count(db, "SELECT count(*) FROM connections WHERE provider = 's3'") == 0


@pytest.mark.req("FR-15.11", "FR-15.12", "SEC-3")
@pytest.mark.wp("P3-13")
async def test_linked_document_is_never_deleted_at_source(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint, clock: FixedClock
) -> None:
    """Decision 89: a linked source is read-only, so the delete-at-source flow refuses a
    document synced from an S3 bucket (409 `linked_source_read_only`) before a token is
    issued or used: no confirmation row, the document stays live, its record is not
    marked, and nothing is audited as deleted at the source."""
    import uuid  # noqa: PLC0415

    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415

    ws = knowledge_ws
    project_id = await _project(ws, clock, "Acme")
    bucket = await new_bucket(minio)
    key = "acme/brief.md"
    await put(minio, bucket, key, b"# Brief\n")
    created = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    await _sync(ws, created.id, ExtractLog([]))
    doc = (await source_documents(ws, created.id))[key]["id"]

    user = uuid.uuid4()
    with pytest.raises(ProblemError) as issued:
        async with tenant_session(ws.ctx) as s:
            await knowledge.issue_delete_confirmation(s, doc, user_id=user)
    assert (issued.value.status, issued.value.code) == (409, "linked_source_read_only")
    with pytest.raises(ProblemError) as used:
        async with tenant_session(ws.ctx) as s:
            await knowledge.delete_at_source(
                s, doc, confirm_token="x" * 32, reason="cleanup", user_id=user
            )
    assert (used.value.status, used.value.code) == (409, "linked_source_read_only")

    confirmations = "SELECT count(*) FROM delete_confirmations WHERE document_id = %s"
    marked = "SELECT count(*) FROM folder_files WHERE document_id = %s AND delete_confirmed"
    live = "SELECT count(*) FROM documents WHERE id = %s AND deleted_at IS NULL"
    audited = "SELECT count(*) FROM audit_log WHERE action = 'knowledge.deleted_at_source'"
    assert _count(db, confirmations, doc) == 0
    assert _count(db, marked, doc) == 0
    assert _count(db, live, doc) == 1
    assert _count(db, audited) == 0


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_failed_sync_keeps_extraction_requests_for_versions_taken_in(
    knowledge_ws: WorkspaceHandle,
    minio: S3Endpoint,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#158 review: a sync that fails after taking some objects in has already asked for
    their extraction, because the retried step finds them unchanged and would not ask
    again; the retry takes in only what is left."""
    s3_sync = importlib.import_module("tumnis.modules.knowledge.s3_sync")
    ws = knowledge_ws
    project_id = await _project(ws, clock, "Acme")
    bucket = await new_bucket(minio)
    await put(minio, bucket, "acme/a.md", b"# A\n")
    await put(minio, bucket, "acme/b.md", b"# B\n")
    created = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    log = ExtractLog([])
    real_take_in = s3_sync._take_in
    calls = 0

    async def failing_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("the S3 read failed")
        return await real_take_in(*args, **kwargs)

    monkeypatch.setattr(s3_sync, "_take_in", failing_second)
    with pytest.raises(RuntimeError):
        await _sync(ws, created.id, log)
    docs = await source_documents(ws, created.id)
    assert set(docs) == {"acme/a.md"}
    assert len(log.requests) == 1

    monkeypatch.setattr(s3_sync, "_take_in", real_take_in)
    await _sync(ws, created.id, log)
    assert set(await source_documents(ws, created.id)) == {"acme/a.md", "acme/b.md"}
    assert len(log.requests) == 2


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_recheck_of_a_key_tumnis_cannot_address_takes_nothing(
    knowledge_ws: WorkspaceHandle, minio: S3Endpoint, clock: FixedClock
) -> None:
    """#158 review: a notification naming a key under a mapped prefix that the path rules
    refuse (`..`) is answered by a recheck that takes nothing in, not by an error."""
    s3_sync = importlib.import_module("tumnis.modules.knowledge.s3_sync")
    ws = knowledge_ws
    project_id = await _project(ws, clock, "Acme")
    bucket = await new_bucket(minio)
    created = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    log = ExtractLog([])
    taken = await s3_sync.recheck_key(
        ws.ctx, created.id, "acme/../secrets.md", net=SELF_HOSTED, extract=log
    )
    assert taken is False
    assert log.requests == []
    assert await source_documents(ws, created.id) == {}


@pytest.mark.req("FR-15.11")
@pytest.mark.wp("P3-13")
async def test_linked_reread_of_a_changed_object_keeps_the_version_hash(
    db: DbUrls, knowledge_ws: WorkspaceHandle, minio: S3Endpoint, clock: FixedClock
) -> None:
    """#158 review: when a linked version's spool copy is gone and its object has changed
    since, reading it again from the bucket fails rather than hand step 1 the new bytes,
    so the version keeps the hash of what was taken in."""
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415

    s3_sync = importlib.import_module("tumnis.modules.knowledge.s3_sync")
    ws = knowledge_ws
    project_id = await _project(ws, clock, "Acme")
    bucket = await new_bucket(minio)
    original = b"# Brief\n\nThe first text.\n"
    await put(minio, bucket, "acme/brief.md", original)
    created = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    log = ExtractLog([])
    await _sync(ws, created.id, log)
    version_id = log.requests[0][1]
    pipeline.spool_path(version_id).unlink(missing_ok=True)
    hashed = "SELECT encode(content_hash, 'hex') FROM document_versions WHERE id = %s"

    def stored_hash() -> str:
        with psycopg.connect(db.libpq(OWNER)) as conn:
            row = conn.execute(hashed, (version_id,)).fetchone()
        assert row is not None
        return str(row[0])

    before = stored_hash()
    s3_sync.configure(SELF_HOSTED)
    try:
        assert b"".join([c async for c in s3_sync.read_linked(ws.ctx, version_id)]) == original
        await put(minio, bucket, "acme/brief.md", b"# Brief\n\nSomething else entirely.\n")
        with pytest.raises(pipeline.LinkedObjectChangedError):
            _ = [c async for c in s3_sync.read_linked(ws.ctx, version_id)]
    finally:
        s3_sync.configure(None)
    assert stored_hash() == before
