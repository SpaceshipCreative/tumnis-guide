"""A focus reply relayed from the master's chat channel (P2-16, FR-8.2, FR-10.4): the
master's `record_human_reply` with item_kind focus answers the event exactly as the app's
one-tap answer does, as the person; only the four one-tap answers are taken.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), level Coach.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, at, rows

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]

MESSAGE_ID = "1290000000000000077"  # an invented chat message id


@asynccontextmanager
async def _master(focus: Focus) -> AsyncIterator[str]:
    """A key with `delegate` that the agent surface treats as the master's (the caller
    facts seam, as the P2-08 taint sweep marks it); its secret."""
    from tumnis.core import agent_surface  # noqa: PLC0415
    from tumnis.modules.auth import api as auth  # noqa: PLC0415

    key = await auth.create_key(
        focus.ctx(),
        auth.KeyIn(name="focus relay master", scopes=["tasks:read", "tasks:write", "delegate"]),
        now=focus.clock.now(),
    )

    async def facts(principal: Any) -> Any:
        if principal.subject_id == key.id:
            return agent_surface.CallerFacts(profile_id=None, is_master=True, run_id=None)
        return None

    agent_surface.register_caller_facts("focus-relay-master", facts)
    try:
        yield key.key
    finally:
        agent_surface.unregister_caller_facts("focus-relay-master")


async def _relay(focus: Focus, key: str, event_id: Any, answer: str) -> Any:
    from tests._mcp import http_for  # noqa: PLC0415

    app: Any = focus.http._transport.app  # type: ignore[attr-defined]  # httpx.ASGITransport
    async with http_for(app, key) as http:
        answered = await http.post(
            "/v1/relay/replies",
            json={
                "item_kind": "focus",
                "item_id": str(event_id),
                "answer": answer,
                "channel_message_id": MESSAGE_ID,
            },
            headers={"Idempotency-Key": f"relay-{uuid.uuid4()}"},
        )
    await focus.settle()
    return answered


@pytest.mark.req("FR-8.2", "FR-10.4")
@pytest.mark.wp("P2-16")
@pytest.mark.xfail(strict=True, reason="spec:P2-16")
async def test_relayed_focus_reply_equals_app_response(dbos: Any, focus: Focus) -> None:
    """A check-in answered `still_on_it` through the relay leaves one `focus_responses` row
    and one `focus.responded` event, both the person's (`user:<id>`), as the app's answer
    does; the relay adds one `human.relayed` audit row with the channel's message id. An
    answer outside the four one-tap answers is refused (422) and records nothing.
    """
    task = await focus.task("Write proposal", first_action="Open the proposal outline")
    await focus.level("coach")
    started = at(TUESDAY, "10:00")
    await focus.advance(started)
    await focus.move(task, "in_progress")
    await focus.advance(started + timedelta(minutes=25))
    [check_in] = focus.events("check_in_due")

    async with _master(focus) as key:
        refused = await _relay(focus, key, check_in["id"], "less_of_this")
        assert refused.status_code == 422, refused.text
        assert rows(focus.db, "SELECT * FROM focus_responses") == []

        answered = await _relay(focus, key, check_in["id"], "still_on_it")
        assert answered.status_code == 200, answered.text
        assert answered.json()["item_kind"] == "focus"

    user = f"user:{focus.workspace.user_id}"
    [response] = rows(focus.db, "SELECT * FROM focus_responses")
    assert (response["event_id"], response["task_id"]) == (check_in["id"], task)
    assert (response["response"], response["created_by"]) == ("still_on_it", user)
    [event] = rows(focus.db, "SELECT payload, actor FROM outbox WHERE name = 'focus.responded'")
    assert event["actor"] == user
    assert event["payload"]["event_id"] == str(check_in["id"])
    assert event["payload"]["response"] == "still_on_it"
    [audit] = rows(
        focus.db,
        "SELECT actor_type, target_id, details FROM audit_log WHERE action = 'human.relayed'",
    )
    assert audit["actor_type"] == "api_key"
    assert audit["target_id"] == check_in["id"]
    assert audit["details"]["item_kind"] == "focus"
    assert audit["details"]["channel_message_id"] == MESSAGE_ID
