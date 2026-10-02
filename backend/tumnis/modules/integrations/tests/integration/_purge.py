"""Helpers for the P3-09 retention and purge tests. No assertions about the product live
here: spec-guard locks the test bodies.

`PurgeWorld` holds one workspace, its signed-in client and the projects archive world
(P2-18's `ArchiveWorld`: projects made and archived through the routes, DBOS running the
workflows in this process), and adds what the purge tests need:

- `connection(account)`: a `connections` row for the scripted email provider.
- `ingest(connection_id, *items, mapper=None)`: one page through `ingest_page` (raw
  payloads and canonical records), with the scripted connector or one using `mapper`.
- `message(external_id, sent_at, ...)` / `note(external_id, start)`: raw items.
- `record_id(table, connection_id, external_id)`: a canonical row's id.
- `link(owner_type, owner_id, target_type, target_id)`: a context item, through the api.
- `task(project_id, done=False)`: a task of the project (done through the owner, as a
  closed task); `link_task(task_id, target_type, target_id)`: the task's own context item,
  linked to the task the way the drawer links it (`POST /v1/tasks/{id}/context-items`).
- `retention(days)`: `PUT /v1/settings/integrations.retention` in days mode.
- `tick_retention()`: `POST /v1/test/tick/retention-purge` (the housekeeping schedule's
  stand-in), then waits until every purge row is `done`.
- `finish(purge_id)`: relays the outbox and waits for workflow `purge:<id>`; then
  `GET /v1/purges/{id}`.
- readers over the owner connection: `ids(table)`, `raw_ids(connection_id)`,
  `audit_rows()`, `outbox(name)`, `context_item(id)`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row

from tests._pg import OWNER
from tumnis.modules.integrations.tests.integration._connections import wait_for
from tumnis.modules.integrations.tests.integration._integrations import new_connection

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from dbos import DBOSClient

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.canonical import CanonicalRecord
    from tumnis.core.clock import FixedClock
    from tumnis.modules.integrations.api import RawItem
    from tumnis.modules.projects.tests.integration._archive import ArchiveWorld

FETCHED = datetime(2026, 3, 9, 11, 0, tzinfo=UTC)
OLD = datetime(2026, 1, 5, 9, 0, tzinfo=UTC)  # 63 days before the test clock
RECENT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)  # 8 days before the test clock
SETTLE_S = 30.0


def owner_rows(db: DbUrls, query: str, *params: Any) -> list[dict[str, Any]]:
    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return conn.execute(query.encode(), params).fetchall()


@dataclass
class PurgeWorld:
    db: DbUrls
    ws: WorkspaceHandle
    clock: FixedClock
    client: SessionClient
    dbos_client: DBOSClient
    archive: ArchiveWorld

    # --- content ----------------------------------------------------------------------------

    def connection(self, account: str) -> uuid.UUID:
        return new_connection(self.db, self.ws.id, provider="scripted", account=account)

    @staticmethod
    def message(
        external_id: str,
        sent_at: datetime,
        *,
        thread: str | None = None,
        sender: str = "sender@example.com",
    ) -> RawItem:
        from tumnis.modules.integrations.api import RawItem  # noqa: PLC0415

        payload: dict[str, Any] = {
            "id": external_id,
            "from": sender,
            "to": ["me@example.org"],
            "subject": f"About {external_id}",
            "text": f"Body of {external_id}",
            "sent_at": sent_at.isoformat(),
        }
        if thread is not None:
            payload["thread"] = {"id": thread, "subject": f"Thread {thread}"}
        return RawItem(
            external_id=external_id, record_type="message", fetched_at=FETCHED, payload=payload
        )

    @staticmethod
    def note(external_id: str, start: datetime) -> RawItem:
        from tumnis.modules.integrations.api import RawItem  # noqa: PLC0415

        return RawItem(
            external_id=external_id,
            record_type="note",
            fetched_at=FETCHED,
            payload={
                "id": external_id,
                "title": f"Meeting {external_id}",
                "start": start.isoformat(),
                "end": start.isoformat(),
                "text": f"Notes of {external_id}",
            },
        )

    async def ingest(
        self,
        connection_id: uuid.UUID,
        *items: RawItem,
        mapper: Callable[[RawItem], Sequence[CanonicalRecord]] | None = None,
    ) -> None:
        from tumnis.modules.integrations import api  # noqa: PLC0415
        from tumnis.modules.integrations.adapters.fake import ScriptedConnector  # noqa: PLC0415

        connector = ScriptedConnector(mapper=mapper)
        page = api.SyncPage(items=list(items), next_cursor=None, has_more=False)
        await api.ingest_page(self.ws.ctx, connection_id, connector, page)

    def record_id(self, table: str, connection_id: uuid.UUID, external_id: str) -> uuid.UUID:
        [row] = owner_rows(
            self.db,
            f"SELECT id FROM {table} WHERE connection_id = %s AND external_id = %s",  # noqa: S608
            connection_id,
            external_id,
        )
        found: uuid.UUID = row["id"]
        return found

    async def link(
        self, owner_type: str, owner_id: uuid.UUID, target_type: str, target_id: uuid.UUID
    ) -> uuid.UUID:
        from tumnis.modules.integrations import api  # noqa: PLC0415

        item = await api.link_context(
            self.ws.ctx,
            owner_type=owner_type,  # type: ignore[arg-type]
            owner_id=owner_id,
            target_type=target_type,  # type: ignore[arg-type]
            target_id=target_id,
            added_by="user",
        )
        return item.id

    async def task(self, project_id: uuid.UUID, *, done: bool = False) -> uuid.UUID:
        from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
        from tumnis.modules.tasks import api as tasks  # noqa: PLC0415

        async with tenant_session(self.ws.ctx) as s:
            made = await tasks.create_task(
                s,
                self.ws.ctx.actor,
                tasks.TaskCreate(
                    project_id=project_id,
                    title=f"Task {uuid.uuid4().hex[:6]}",
                    label="human",
                    estimate_minutes=30,
                ),
                now=self.clock.now(),
            )
        if done:
            with psycopg.connect(self.db.libpq(OWNER)) as conn:
                conn.execute(
                    "UPDATE tasks SET status = 'done', completed_at = %s WHERE id = %s",
                    (self.clock.now(), made.id),
                )
        return made.id

    async def link_task(
        self, task_id: uuid.UUID, target_type: str, target_id: uuid.UUID
    ) -> uuid.UUID:
        item_id = await self.link("task", task_id, target_type, target_id)
        linked = await self.client.post(
            f"/v1/tasks/{task_id}/context-items", json={"context_item_id": str(item_id)}
        )
        linked.raise_for_status()
        return item_id

    # --- the product ------------------------------------------------------------------------

    async def retention(self, days: int) -> None:
        current = await self.client.get("/v1/settings/integrations.retention")
        current.raise_for_status()
        saved = await self.client.put(
            "/v1/settings/integrations.retention",
            json={"values": {"mode": "days", "days": days}, "version": current.json()["version"]},
        )
        saved.raise_for_status()

    async def tick_retention(self) -> None:
        ticked = await self.client.post("/v1/test/tick/retention-purge")
        ticked.raise_for_status()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        while True:
            await self.archive.relay()
            flows = await self.dbos_client.list_workflows_async(
                name=["integrations_retention_purge", "integrations_purge_scope"],
                status=["ENQUEUED", "PENDING"],
            )
            open_purges = owner_rows(self.db, "SELECT id FROM purges WHERE status <> 'done'")
            ran = await self.dbos_client.list_workflows_async(name="integrations_retention_purge")
            if ran and not flows and not open_purges:
                return
            if loop.time() > deadline:
                raise AssertionError("the retention purge did not finish")
            await asyncio.sleep(0.1)

    async def purge(self, scope: str, target_id: uuid.UUID, reason: str | None) -> Any:
        body: dict[str, Any] = {"scope": scope, "id": str(target_id)}
        if reason is not None:
            body["reason"] = reason
        return await self.client.post("/v1/purges", json=body)

    async def finish(self, purge_id: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SETTLE_S
        while True:
            await self.archive.relay()
            flows = await self.dbos_client.list_workflows_async(workflow_ids=[f"purge:{purge_id}"])
            if flows:
                break
            if loop.time() > deadline:
                raise AssertionError(f"purge:{purge_id} never started")
            await asyncio.sleep(0.1)
        await wait_for(self.dbos_client, f"purge:{purge_id}")
        found = await self.client.get(f"/v1/purges/{purge_id}")
        found.raise_for_status()
        result: dict[str, Any] = found.json()
        return result

    # --- readers ----------------------------------------------------------------------------

    def ids(self, table: str) -> set[uuid.UUID]:
        return {r["id"] for r in owner_rows(self.db, f"SELECT id FROM {table}")}  # noqa: S608

    def external_ids(self, table: str, connection_id: uuid.UUID) -> set[str]:
        return {
            r["external_id"]
            for r in owner_rows(
                self.db,
                f"SELECT external_id FROM {table} WHERE connection_id = %s",  # noqa: S608
                connection_id,
            )
        }

    def raw_ids(self, connection_id: uuid.UUID) -> set[str]:
        """(record type, external id) of the connection's raw payloads, as 'type:id'."""
        return {
            f"{r['record_type']}:{r['external_id']}"
            for r in owner_rows(
                self.db,
                "SELECT record_type, external_id FROM raw_payloads WHERE connection_id = %s",
                connection_id,
            )
        }

    def audit_rows(self) -> list[dict[str, Any]]:
        return owner_rows(
            self.db,
            "SELECT actor_type, target_type, target_id, reason, details FROM audit_log"
            " WHERE action = 'data.purged' ORDER BY seq",
        )

    def outbox(self, name: str) -> list[dict[str, Any]]:
        """The outbox rows of event `name`, oldest first: `event_id` and `payload`."""
        return owner_rows(
            self.db, "SELECT event_id, payload FROM outbox WHERE name = %s ORDER BY id", name
        )

    def context_item(self, item_id: uuid.UUID) -> dict[str, Any]:
        [row] = owner_rows(self.db, "SELECT * FROM context_items WHERE id = %s", item_id)
        return row
