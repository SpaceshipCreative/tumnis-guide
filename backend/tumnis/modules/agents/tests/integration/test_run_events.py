"""Run events storage and paging (P2-04, FR-5.5): what the runner streams is stored once
per message id and read back by `GET /v1/runs/{id}/events?after_seq=&limit=`, in order."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._runs import (
    owner_rows,
    relay,
    run_world,
    stream,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fakes.fake_runner import FakeRunnerFactory
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

LINES = ["Checking out fix-footer", "git.checkout fix-footer", "Editing src/footer.tsx"]


@pytest.mark.req("FR-5.5")
@pytest.mark.wp("P2-04")
async def test_events_paged_in_order_and_deduped(  # noqa: PLR0917
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-04-13
    A stream line resent with the same `message_id` is stored once; paging the run's
    events two at a time with `after_seq` returns every event exactly once, in the order
    they were stored, with increasing `seq`, and an empty page at the end.
    """
    world = await run_world(fake_runner, workspace, clock)
    task = await world.ai_task("Fix footer link")
    async with relay(db):
        run_id = await world.request(task.id)
        world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
        ids = []
        for seq, text in enumerate(LINES, start=1):
            ids.append(stream(world.runner, run_id, seq, text, kind="log"))
            assert await wait_until(
                lambda: (
                    owner_rows(
                        db, "SELECT count(*) FROM run_events WHERE message_id = %s", (ids[-1],)
                    )
                    == [(1,)]
                )
            )
        stream(world.runner, run_id, 2, LINES[1], message_id=ids[1])  # a replay
        world.runner.wait_for(lambda r: r.ack_log.count(ids[1]) >= 2)

    stored = owner_rows(
        db, "SELECT message_id FROM run_events WHERE run_id = %s ORDER BY seq", (run_id,)
    )
    assert len(stored) == len(set(stored))

    seen: list[dict[str, Any]] = []
    after: int | None = None
    for _ in range(10):
        params = {"limit": 2} if after is None else {"limit": 2, "after_seq": after}
        page = await session_client.get(f"/v1/runs/{run_id}/events", params=params)
        assert page.status_code == 200, page.text
        items = page.json()["items"]
        if not items:
            break
        assert len(items) <= 2
        seen += items
        after = items[-1]["seq"]
    seqs = [event["seq"] for event in seen]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))
    assert [str(event["message_id"]) for event in seen] == [str(m) for (m,) in stored]
    texts = [event["payload"].get("text") for event in seen if event["kind"] == "log"]
    assert texts == LINES
