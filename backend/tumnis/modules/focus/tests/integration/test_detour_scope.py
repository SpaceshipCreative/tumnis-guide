"""Which detour a Return/Stay answer applies to (P4-01, FR-10.6; PR #142 review): only the
question `GET /v1/focus/current` shows, today's open one, and, when the answer names its
`event_id`, only that question.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), plan from 09:00.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import WEDNESDAY, at
from tumnis.modules.focus.tests.integration._guardrail import (
    detour,
    guardrail_day,
    task_row,
    tasks_titled,
)

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BANK = "Call the bank about the card"


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
async def test_yesterdays_open_detour_is_not_answered(dbos: Any, focus: Focus) -> None:
    """A question left open yesterday is not shown today, so an answer today is 409
    `no_open_detour` and moves nothing."""
    admin = await focus.project("Admin")
    day = await guardrail_day(focus)
    assert (await detour(focus, BANK, admin)).status_code == 200
    [made] = tasks_titled(focus, BANK)

    await focus.advance(at(WEDNESDAY, "09:00"))
    assert (await focus.current())["detour"] is None
    before = task_row(focus, made["id"])
    answer = await focus.http.post(
        "/v1/focus/return", json={"decision": "return", "version": before["version"]}
    )
    await focus.settle()

    assert answer.status_code == 409, answer.text
    assert answer.json()["code"] == "no_open_detour"
    assert task_row(focus, made["id"])["status"] == before["status"]
    assert task_row(focus, day.a)["status"] != "in_progress"
    [switched] = focus.events("switched")
    assert switched["return_decision"] is None


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
async def test_answer_applies_only_to_the_named_detour(dbos: Any, focus: Focus) -> None:
    """An answer naming another focus event than the open question is 409
    `no_open_detour`, one naming no focus event is 404; the open question's own `event_id`
    answers it."""
    admin = await focus.project("Admin")
    await guardrail_day(focus)
    assert (await detour(focus, BANK, admin)).status_code == 200
    [made] = tasks_titled(focus, BANK)
    shown = (await focus.current())["detour"]
    [block_start, *_] = focus.events("block_start")

    wrong = await focus.http.post(
        "/v1/focus/return",
        json={"decision": "stay", "version": 1, "event_id": str(block_start["id"])},
    )
    assert wrong.status_code == 409, wrong.text
    assert wrong.json()["code"] == "no_open_detour"
    unknown = await focus.http.post(
        "/v1/focus/return",
        json={"decision": "stay", "version": 1, "event_id": str(made["id"])},
    )
    assert unknown.status_code == 404, unknown.text
    assert focus.events("switched")[0]["return_decision"] is None

    right = await focus.http.post(
        "/v1/focus/return",
        json={"decision": "stay", "version": 1, "event_id": shown["event_id"]},
    )
    await focus.settle()
    assert right.status_code == 200, right.text
    assert focus.events("switched")[0]["return_decision"] == "stay"


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
async def test_detour_pairing_is_422_after_the_event_is_found(dbos: Any, focus: Focus) -> None:
    """A detour with a response other than `switched`, or with `to_task_id`, is 422
    `invalid_detour` and creates nothing; with an unknown event it is 404 first."""
    admin = await focus.project("Admin")
    day = await guardrail_day(focus)
    [event] = focus.events("block_start")[:1]
    detour_body = {"title": BANK, "project_id": str(admin)}

    for extra in ({"response": "still_on_it"}, {"response": "switched", "to_task_id": str(day.b)}):
        answer = await focus.http.post(
            "/v1/focus/respond", json={"event_id": str(event["id"]), "detour": detour_body, **extra}
        )
        assert answer.status_code == 422, answer.text
        assert answer.json()["code"] == "invalid_detour"
    unknown = await focus.http.post(
        "/v1/focus/respond",
        json={"event_id": str(day.a), "response": "still_on_it", "detour": detour_body},
    )
    assert unknown.status_code == 404, unknown.text
    assert tasks_titled(focus, BANK) == []
