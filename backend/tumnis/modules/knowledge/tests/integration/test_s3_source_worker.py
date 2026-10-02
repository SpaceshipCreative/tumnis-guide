"""An S3 source sync in a real worker process queues each file's extraction
(APP-TEST-final finding 1).

The other S3 tests run the sync in the test process, where the `extract_env` fixture has
configured the api's DBOS client, so they never saw that `tumnis worker` itself did not:
on the production image every sync failed to queue its extraction and the documents
stayed `pending_scan`. Here a worker subprocess starts the way `tumnis worker` does
(`tumnis.worker.main`; no api has run) and syncs a bucket on the MinIO container.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import psycopg
import pytest

from tests._pg import OWNER
from tests.fixtures import KILLER_APP_VERSION
from tumnis.modules.knowledge.tests.integration._s3_source import (
    create_source,
    new_bucket,
    put,
    source_documents,
    source_in,
)

if TYPE_CHECKING:
    from uuid import UUID

    from tests._pg import DbUrls
    from tests._services import S3Endpoint
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SYNC_QUEUE = "sync"
S3_SYNC_WORKFLOW = "knowledge_s3_source_sync"
EXTRACT_WORKFLOW = "knowledge_extract_document"


async def _project(ws: WorkspaceHandle, clock: FixedClock) -> UUID:
    """A project with its folder record, for the bucket prefix to map to."""
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
    from tumnis.modules.projects import api as projects  # noqa: PLC0415

    async with tenant_session(ws.ctx) as s:
        project = await projects.create_project(
            s, ws.ctx.actor, projects.ProjectCreate(name="Acme"), now=clock.now()
        )
        await knowledge.assign_project_folder(s, project.id)
    return project.id


def _version_ids(db: DbUrls, document_id: UUID) -> list[str]:
    """The ids of the document's versions (read as the owner role)."""
    with psycopg.connect(db.libpq(OWNER)) as conn:
        found = conn.execute(
            "SELECT id FROM document_versions WHERE document_id = %s", (document_id,)
        ).fetchall()
    return [str(row[0]) for row in found]


@pytest.mark.req("FR-15.11", "REL-3")
@pytest.mark.wp("P3-13")
async def test_app_test_final_1_worker_s3_sync_queues_extraction(
    knowledge_ws: WorkspaceHandle,
    worker_killer: WorkerKillerFactory,
    minio: S3Endpoint,
    db: DbUrls,
    clock: FixedClock,
) -> None:
    """APP-TEST-final finding 1
    A worker started as `tumnis worker` is, in a process where no api ever ran, runs a
    linked S3 source's sync to the end: the object is taken in as a document and its
    extraction is queued on the `extract` queue (`extract:<version id>`), where the extract
    worker would take it. Before the fix the sync failed on "no DBOS system database URL".
    """
    ws = knowledge_ws
    project_id = await _project(ws, clock)
    bucket = await new_bucket(minio)
    await put(minio, bucket, "acme/brief.md", b"# Brief\n\nThe Acme site.\n")
    source = await create_source(ws, source_in(minio, bucket, {"acme/": project_id}))
    killer = worker_killer("never", events=0)
    workflow_id = f"s3-sync-test:{source.id}"
    await killer.dbos_client().enqueue_async(
        {
            "queue_name": SYNC_QUEUE,
            "workflow_name": S3_SYNC_WORKFLOW,
            "workflow_id": workflow_id,
            "app_version": KILLER_APP_VERSION,
        },
        str(ws.id),
        str(source.id),
    )

    status = await killer.restart_until_done(workflow_id)

    assert status == "SUCCESS", killer.log_tail()
    docs = await source_documents(ws, source.id)
    assert set(docs) == {"acme/brief.md"}
    versions = _version_ids(db, docs["acme/brief.md"]["id"])
    assert len(versions) == 1
    queued = await asyncio.to_thread(killer.dbos_client().list_workflows, name=EXTRACT_WORKFLOW)
    assert {w.workflow_id for w in queued} == {f"extract:{versions[0]}"}
