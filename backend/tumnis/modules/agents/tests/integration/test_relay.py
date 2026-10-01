"""Answers relayed from the master's chat channel (P2-16, FR-8.2): `record_human_reply`, a
master-only tool, records a question's answer exactly as the app does (the same rows and
the same `human.decided`, as the person), plus one audit row naming the channel and its
message. Approvals and results are decided in the app only: 403 `needs_app`.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.agents.tests.integration._human import (
    approval,
    ask,
    audit_rows,
    decide,
    human_waits,
    open_items,
    started,
)
from tumnis.modules.agents.tests.integration._runs import (
    call_tool,
    finish,
    master_key,
    owner_rows,
    relay,
    run_world,
    wait_until,
)

if TYPE_CHECKING:
    from dbos import DBOS
    from fastapi import FastAPI

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

QUESTION = "Which footer color?"
CHOICES = ["Navy", "Teal"]
MESSAGE_ID = "1290000000000000042"  # an invented chat message id


def _answered(db: DbUrls, item_id: Any) -> dict[str, Any]:
    """What answering left behind, without the ids and versions that differ between two
    questions: the question row, the review item's decision, and the `human.decided`
    event with the actor that emitted it."""
    [(status, answer, decided_by, decided_at)] = owner_rows(
        db,
        "SELECT status, answer, decided_by, decided_at FROM questions WHERE review_item_id = %s",
        (item_id,),
    )
    [(decision, item_decided_at)] = owner_rows(
        db, "SELECT decision, decided_at FROM review_items WHERE id = %s", (item_id,)
    )
    [(payload, actor)] = owner_rows(
        db,
        "SELECT payload, actor FROM outbox WHERE name = 'human.decided'"
        " AND payload->>'item_id' = %s",
        (str(item_id),),
    )
    event = {k: v for k, v in payload.items() if k not in {"item_id", "target_id"}}
    return {
        "question": (status, answer, decided_by, decided_at),
        "item": (decision, item_decided_at),
        "event": event,
        "actor": actor,
    }


@pytest.mark.req("FR-8.2")
@pytest.mark.wp("P2-16")
async def test_discord_answer_equals_app_answer(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    session_client: SessionClient,
) -> None:
    """T-P2-16-07
    The same question, asked by two runs, is answered once in the app and once through the
    master's `record_human_reply` (item_kind question, the review item's id, the channel's
    message id): the question rows, the review items' decisions and the `human.decided`
    events are equal apart from their ids, and both are the person's (`user:<id>`). Only
    the relayed answer leaves a `human.relayed` audit row: the master's key, channel
    `discord` and the message id.
    """
    world = await run_world(fake_runner, workspace, clock)
    master = await master_key(world)
    in_app = await world.ai_task("Pick the footer color")
    via_chat = await world.ai_task("Pick the header color")
    with human_waits(poll_seconds=1):
        async with relay(db):
            items = []
            for task in (in_app, via_chat):
                run_id = await started(world, db, task.id)
                asked = await ask(app, world.token(run_id), run_id, QUESTION, choices=CHOICES)
                assert asked.status_code == 200, asked.text
                [item] = [i for i in open_items(db, "question") if i["target_id"] == task.id]
                items.append(item)
            app_item, chat_item = items

            decided = await decide(session_client, app_item, "answer", {"answer": "Navy"})
            assert decided.status_code == 200, decided.text
            relayed = await call_tool(
                world.runner,
                master,
                "record_human_reply",
                {
                    "item_kind": "question",
                    "item_id": str(chat_item["id"]),
                    "answer": "Navy",
                    "channel_message_id": MESSAGE_ID,
                },
            )
            assert relayed.ok, relayed

            app_side = _answered(db, app_item["id"])
            chat_side = _answered(db, chat_item["id"])
            assert app_side == chat_side
            assert app_side["actor"] == f"user:{workspace.user_id}"
            assert app_side["question"][:3] == ("answered", "Navy", f"user:{workspace.user_id}")

    [row] = audit_rows(db, "human.relayed")
    assert row["actor_type"] == "api_key"
    assert row["target_id"] == str(chat_item["id"])
    assert row["details"]["channel"] == "discord"
    assert row["details"]["channel_message_id"] == MESSAGE_ID
    assert row["details"]["item_kind"] == "question"
    assert not [r for r in audit_rows(db, "human.relayed") if r["target_id"] == str(app_item["id"])]


@pytest.mark.req("FR-8.2")
@pytest.mark.wp("P2-16")
async def test_relay_refuses_approvals_and_results(  # noqa: PLR0917
    app: FastAPI,
    dbos: type[DBOS],
    fake_runner: FakeRunnerFactory,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
) -> None:
    """T-P2-16-08
    An approval or a result is decided in the app only: `record_human_reply` answers 403
    `needs_app` for item_kind approval and result, and for a question reply naming an
    approval's review item, on both doors; the items stay open.
    """
    from tests._mcp import http_for  # noqa: PLC0415

    world = await run_world(fake_runner, workspace, clock)
    master = await master_key(world)
    gated = await world.ai_task("Merge the footer fix")
    done = await world.ai_task("Fix the footer link")
    with human_waits(poll_seconds=1):
        async with relay(db):
            run_id = await started(world, db, gated.id)
            asked = await approval(app, world.token(run_id), run_id, "merge_main", "Merge PR #7")
            assert asked.status_code == 200, asked.text
            [approval_item] = open_items(db, "approval")

            result_run = await started(world, db, done.id)
            finish(world.runner, result_run)
            await wait_until(lambda: len(open_items(db, "result")) == 1)
            [result_item] = open_items(db, "result")

            attempts = [
                ("approval", approval_item["id"], "approve"),
                ("result", result_item["id"], "accept"),
                ("question", approval_item["id"], "approve"),
            ]
            for kind, item_id, answer in attempts:
                args = {
                    "item_kind": kind,
                    "item_id": str(item_id),
                    "answer": answer,
                    "channel_message_id": MESSAGE_ID,
                }
                via_tool = await call_tool(world.runner, master, "record_human_reply", args)
                assert via_tool.code == "needs_app", (kind, via_tool)
                async with http_for(app, master) as http:
                    via_rest = await http.post(
                        "/v1/relay/replies",
                        json=args,
                        headers={"Idempotency-Key": f"relay-{uuid.uuid4()}"},
                    )
                assert via_rest.status_code == 403, via_rest.text
                assert via_rest.json()["code"] == "needs_app"

            assert [i["id"] for i in open_items(db, "approval")] == [approval_item["id"]]
            assert [i["id"] for i in open_items(db, "result")] == [result_item["id"]]
    assert audit_rows(db, "human.relayed") == []
