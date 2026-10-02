"""Retention and purge (P3-09, SAAS-2, FR-5.10, Data flow rule 3, REL-3).

The workspace retention setting (`integrations.retention`) purges old ingested email,
chat and notes on the housekeeping schedule; `POST /v1/purges` purges an archived
project's content or one connection's; each purge deletes the canonical rows (messages,
threads, notes) and their raw payloads in batches, marks the context items that pointed at
them purged, and is audited once (`data.purged`) with its reason and counts.
`GET /v1/purges/{id}` shows where it stands. Nothing is ever deleted at the provider.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.integrations.tests.integration._purge import (
    OLD,
    RECENT,
    PurgeWorld,
    owner_rows,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from dbos import DBOS, DBOSClient

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkerKillerFactory, WorkspaceHandle
    from tumnis.core.canonical import CanonicalRecord
    from tumnis.core.clock import FixedClock
    from tumnis.modules.integrations.api import RawItem

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

PROBE = "tumnis.modules.integrations.tests.integration._purge_probe"


@pytest.fixture
async def world(  # noqa: PLR0917
    app_db: DbUrls,
    dbos: type[DBOS],
    dbos_client: DBOSClient,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
    tmp_path: Path,
) -> AsyncIterator[PurgeWorld]:
    from tumnis.modules.projects.tests.integration._archive import ArchiveWorld  # noqa: PLC0415

    archive = ArchiveWorld(
        db=app_db, ws=workspace, clock=clock, client=session_client, root=tmp_path / "location"
    )
    await archive.start()
    try:
        yield PurgeWorld(
            db=app_db,
            ws=workspace,
            clock=clock,
            client=session_client,
            dbos_client=dbos_client,
            archive=archive,
        )
    finally:
        archive.close()


def _counts(**found: int) -> dict[str, int]:
    return {
        "messages": 0,
        "threads": 0,
        "notes": 0,
        "raw_payloads": 0,
        "context_items": 0,
        **found,
    }


@pytest.mark.req("SAAS-2", "Data flow rule 3")
@pytest.mark.wp("P3-09")
async def test_retention_purge_removes_old_content_and_raw_payloads(world: PurgeWorld) -> None:
    """T-P3-09-02
    `retention_purge` runs on the housekeeping schedule (`retention-purge`, `17 * * * *`,
    maintenance queue). With the default setting it purges nothing and audits nothing.
    With 30 days it removes the messages, notes and threads older than the cutoff, with
    their raw payloads, and keeps the recent ones, a thread that still holds a recent
    message, and an old message linked to an open task. One `data.purged` row (system,
    with its reason and counts), `items.purged` events naming exactly the purged records
    (what the search and embedding indexes drop), and a second run changes nothing.
    """
    from tumnis.core.workflows_ops import MAINTENANCE_QUEUE  # noqa: PLC0415
    from tumnis.modules.integrations import workflows  # noqa: PLC0415

    schedule = {s["schedule_name"]: s for s in workflows.schedules()}["retention-purge"]
    assert schedule["schedule"] == "17 * * * *"
    assert schedule["queue_name"] == MAINTENANCE_QUEUE

    conn = world.connection("inbox-a")
    await world.ingest(
        conn,
        world.message("old-1", OLD, thread="t-old"),
        world.message("old-2", OLD),
        world.message("old-3", OLD, thread="t-mix"),
        world.message("recent-1", RECENT, thread="t-mix"),
        world.message("old-open", OLD),
        world.note("n-old", OLD),
        world.note("n-recent", RECENT),
    )
    project = await world.archive.project("Retention")
    task = await world.task(project)
    await world.link_task(task, "message", world.record_id("messages", conn, "old-open"))
    purged = {
        ("message", world.record_id("messages", conn, e)) for e in ("old-1", "old-2", "old-3")
    }
    purged |= {("thread", world.record_id("threads", conn, "t-old"))}
    purged |= {("note", world.record_id("notes", conn, "n-old"))}

    await world.tick_retention()  # the default: keep until the project is purged
    assert world.external_ids("messages", conn) == {
        "old-1",
        "old-2",
        "old-3",
        "recent-1",
        "old-open",
    }
    assert world.audit_rows() == []
    assert owner_rows(world.db, "SELECT id FROM purges") == []

    await world.retention(30)
    await world.tick_retention()

    assert world.external_ids("messages", conn) == {"recent-1", "old-open"}
    assert world.external_ids("threads", conn) == {"t-mix"}
    assert world.external_ids("notes", conn) == {"n-recent"}
    assert world.raw_ids(conn) == {"message:recent-1", "message:old-open", "note:n-recent"}

    counts = _counts(messages=3, threads=1, notes=1, raw_payloads=4)
    [audit] = world.audit_rows()
    assert audit["actor_type"] == "system"
    assert "30 days" in audit["reason"]
    assert audit["details"]["scope"] == "retention"
    assert audit["details"]["counts"] == counts

    [purge] = owner_rows(world.db, "SELECT id, scope, status, counts FROM purges")
    assert (purge["scope"], purge["status"], purge["counts"]) == ("retention", "done", counts)
    shown = await world.client.get(f"/v1/purges/{purge['id']}")
    assert shown.status_code == 200, shown.text
    assert shown.json()["status"] == "done"
    assert shown.json()["counts"] == counts

    events = [e["payload"] for e in world.outbox("items.purged")]
    assert {(e["record_type"], uuid.UUID(i)) for e in events for i in e["ids"]} == purged

    await world.tick_retention()  # nothing left past the cutoff: no new purge, no audit
    assert len(world.audit_rows()) == 1
    assert len(owner_rows(world.db, "SELECT id FROM purges")) == 1


@pytest.mark.req("FR-5.10", "SEC-3")
@pytest.mark.wp("P3-09")
@pytest.mark.xfail(strict=True, reason="spec:P3-09")
async def test_project_purge_removes_content_and_is_audited(world: PurgeWorld) -> None:
    """T-P3-09-03
    An archived project's content: the records its own context items and its tasks' point
    at. A purge without a reason (missing or blank) is a 422 and records nothing. With one
    it is accepted (202, with the purge's id), audited once with the reason and the
    counts, and the worker removes the project's messages and notes with their raw
    payloads. A message another project's task also links stays, and so does content no
    project matched. `GET /v1/purges/{id}` ends `done` with the same counts.
    """
    conn = world.connection("inbox-p")
    await world.ingest(
        conn,
        world.message("m-proj", OLD),
        world.message("m-task", RECENT),
        world.message("m-shared", RECENT),
        world.message("m-other", RECENT),
        world.note("n-task", RECENT),
    )
    acme = await world.archive.project("Acme site")
    other = await world.archive.project("Other")
    await world.link("project", acme, "message", world.record_id("messages", conn, "m-proj"))
    acme_task = await world.task(acme)
    await world.link_task(acme_task, "message", world.record_id("messages", conn, "m-task"))
    await world.link_task(acme_task, "note", world.record_id("notes", conn, "n-task"))
    shared = world.record_id("messages", conn, "m-shared")
    await world.link_task(acme_task, "message", shared)
    await world.link_task(await world.task(other), "message", shared)
    await world.archive.archive(acme)

    for reason in (None, "", "   "):
        refused = await world.purge("project", acme, reason)
        assert refused.status_code == 422, refused.text
    assert world.audit_rows() == []
    assert owner_rows(world.db, "SELECT id FROM purges") == []

    accepted = await world.purge("project", acme, "Client asked")
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert (body["scope"], body["id"], body["status"]) == ("project", str(acme), "accepted")

    counts = _counts(messages=2, notes=1, raw_payloads=3, context_items=2)
    [audit] = world.audit_rows()
    assert audit["actor_type"] == "user"
    assert (audit["target_type"], audit["target_id"]) == ("project", acme)
    assert audit["reason"] == "Client asked"
    assert audit["details"]["counts"] == counts
    assert audit["details"]["purge_id"] == body["purge_id"]

    done = await world.finish(body["purge_id"])
    assert done["status"] == "done"
    assert done["scope"] == "project"
    assert done["target_id"] == str(acme)
    assert done["counts"] == counts

    assert world.external_ids("messages", conn) == {"m-shared", "m-other"}
    assert world.external_ids("notes", conn) == set()
    assert world.raw_ids(conn) == {"message:m-shared", "message:m-other"}
    assert len(world.audit_rows()) == 1


def _with_sender(raw: RawItem) -> Sequence[CanonicalRecord]:
    """The scripted mapping, plus the sender as a person made from the same raw item."""
    from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415
    from tumnis.modules.integrations.api import PersonRecord  # noqa: PLC0415

    records: list[Any] = ScriptedConnector().map(raw)
    sender = raw.payload.get("from")
    if sender:
        records.append(
            PersonRecord(
                external_id=f"person:{sender}",
                fetched_at=raw.fetched_at,
                display_name="Sender",
                primary_email=sender,
                emails=[sender],
                domains=[sender.rpartition("@")[2]],
            )
        )
    return records


@pytest.mark.req("Data flow rule 3")
@pytest.mark.wp("P3-09")
@pytest.mark.xfail(strict=True, reason="spec:P3-09")
async def test_connection_purge_scoped_to_source(world: PurgeWorld) -> None:
    """T-P3-09-04
    Purging one connection removes its messages, threads and notes and their raw payloads,
    and nothing of another mailbox: the same email synced into a second connection stays,
    with its raw payload. A person made from a purged message's raw item stays (it is
    workspace-wide), no longer pointing at the removed payload. The connection itself
    stays as it was. An unknown connection is a 404.
    """
    first = world.connection("inbox-one")
    second = world.connection("inbox-two")
    await world.ingest(
        first,
        world.message("ext-1", RECENT, thread="th-1"),
        world.message("ext-2", RECENT),
        world.note("n-1", RECENT),
        mapper=_with_sender,
    )
    await world.ingest(second, world.message("ext-1", RECENT, thread="th-1"))

    missing = await world.purge("connection", uuid.uuid4(), "Not mine")
    assert missing.status_code == 404, missing.text

    accepted = await world.purge("connection", first, "Mailbox closed")
    assert accepted.status_code == 202, accepted.text
    purge_id = accepted.json()["purge_id"]
    [audit] = world.audit_rows()
    assert (audit["target_type"], audit["target_id"]) == ("connection", first)
    assert audit["reason"] == "Mailbox closed"
    counts = _counts(messages=2, threads=1, notes=1, raw_payloads=3)
    assert audit["details"]["counts"] == counts

    done = await world.finish(purge_id)
    assert (done["status"], done["counts"]) == ("done", counts)

    for table in ("messages", "threads", "notes"):
        assert world.external_ids(table, first) == set(), table
    assert world.raw_ids(first) == set()
    assert world.external_ids("messages", second) == {"ext-1"}
    assert world.external_ids("threads", second) == {"th-1"}
    assert world.raw_ids(second) == {"message:ext-1"}
    [person] = owner_rows(world.db, "SELECT primary_email, raw_payload_id FROM people")
    assert person["primary_email"] == "sender@example.com"
    assert person["raw_payload_id"] is None
    [conn] = owner_rows(world.db, "SELECT status, deleted_at FROM connections WHERE id = %s", first)
    assert (conn["status"], conn["deleted_at"]) == ("ok", None)


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P3-09")
@pytest.mark.xfail(strict=True, reason="spec:P3-09")
async def test_archived_project_is_compressed_not_purged(world: PurgeWorld) -> None:
    """T-P3-09-05
    Retention skips an archived project's content: the old message its own (now archived)
    context item points at and the old message a closed task of the project links both
    stay, while an old message no project holds goes. The archive state and its blobs are
    untouched.
    """
    conn = world.connection("inbox-arch")
    await world.ingest(
        conn,
        world.message("m-arch", OLD),
        world.message("m-arch-task", OLD),
        world.message("m-free", OLD),
    )
    project = await world.archive.project("Archived client")
    await world.link("project", project, "message", world.record_id("messages", conn, "m-arch"))
    closed = await world.task(project, done=True)
    await world.link_task(closed, "message", world.record_id("messages", conn, "m-arch-task"))
    await world.archive.archive(project)
    blobs = world.archive.blobs(project)
    assert blobs  # the project's excerpts were compressed

    await world.retention(30)
    await world.tick_retention()

    assert world.external_ids("messages", conn) == {"m-arch", "m-arch-task"}
    assert world.archive.archive_state(project) == "archived"
    assert world.archive.blobs(project) == blobs


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P3-09")
@pytest.mark.xfail(strict=True, reason="spec:P3-09")
async def test_tasks_keep_link_marked_purged(world: PurgeWorld) -> None:
    """T-P3-09-06
    A closed task's old message is purged by retention, but the task keeps its link: the
    ContextItem stays (not deleted) with `target_purged_at` set, reads as `purged`, and
    shows "Removed by retention" where its text was (the packet and the drawer).
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api  # noqa: PLC0415

    conn = world.connection("inbox-task")
    await world.ingest(conn, world.message("m-done", OLD))
    project = await world.archive.project("Live")
    closed = await world.task(project, done=True)
    item_id = await world.link_task(closed, "message", world.record_id("messages", conn, "m-done"))

    await world.retention(30)
    await world.tick_retention()

    assert world.external_ids("messages", conn) == set()
    row = world.context_item(item_id)
    assert row["deleted_at"] is None
    assert row["target_purged_at"] is not None
    links = owner_rows(
        world.db,
        "SELECT deleted_at FROM task_context_items WHERE task_id = %s AND context_item_id = %s",
        closed,
        item_id,
    )
    assert links == [{"deleted_at": None}]
    async with tenant_session(world.ws.ctx) as s:
        ref = await api.get_context_item_ref(world.ws.ctx, item_id, session=s)
        [text] = await api.context_item_texts(s, [item_id])
    assert ref is not None
    assert ref.purged is True
    assert text.text == "Removed by retention"


@pytest.mark.req("REL-3")
@pytest.mark.wp("P3-09")
@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason="spec:P3-09")
async def test_purge_resumes_after_kill(
    app_db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    worker_killer: WorkerKillerFactory,
) -> None:
    """T-P3-09-07
    A connection purge of five messages in batches of two: a worker killed inside the
    second batch, before its commit, leaves the first batch purged and counted. The
    restarted worker finishes the purge once: every message and raw payload gone, the
    counts exact (5 and 5, nothing counted twice), status `done`, one audit row.
    """
    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.integrations import api  # noqa: PLC0415
    from tumnis.modules.integrations.tests.integration._integrations import (  # noqa: PLC0415
        new_connection,
    )

    conn = new_connection(app_db, workspace.id, provider="scripted", account="inbox-kill")
    world = PurgeWorld(
        db=app_db,
        ws=workspace,
        clock=clock,
        client=None,  # type: ignore[arg-type]  # this test drives no route
        dbos_client=None,  # type: ignore[arg-type]
        archive=None,  # type: ignore[arg-type]
    )
    await world.ingest(conn, *(world.message(f"k-{n}", RECENT) for n in range(1, 6)))
    async with tenant_session(workspace.ctx) as s:
        accepted = await api.purge(
            s, api.PurgeIn(scope="connection", id=conn, reason="Kill test"), now=clock.now()
        )
    purge_id = str(accepted.purge_id)

    killer = worker_killer("integrations.purge.batch_2.committing", events=0, imports=(PROBE,))
    code = await killer.enqueue_until_killed(
        queue_name="maintenance",
        workflow_name="integrations_purge_scope",
        workflow_id=f"purge:{purge_id}",
        args=(str(workspace.id), purge_id),
    )
    assert code == 137, killer.log_tail()
    assert len(world.external_ids("messages", conn)) == 3
    [mid] = owner_rows(app_db, "SELECT status, counts FROM purges WHERE id = %s", purge_id)
    assert mid["counts"]["messages"] == 2
    assert mid["status"] == "running"

    status = await killer.restart_until_done(f"purge:{purge_id}")
    assert status == "SUCCESS", killer.log_tail()
    assert world.external_ids("messages", conn) == set()
    assert world.raw_ids(conn) == set()
    [end] = owner_rows(app_db, "SELECT status, counts FROM purges WHERE id = %s", purge_id)
    assert end["status"] == "done"
    assert end["counts"] == _counts(messages=5, raw_payloads=5)
    assert len(world.audit_rows()) == 1
