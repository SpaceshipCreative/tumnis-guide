"""Detour capture at Guardrail (P4-01, FR-10.6, FR-10.2): "Switched" to something not in Today
creates that task in the project the person picked and asks whether to return; Return and
Stay put the two tasks where they belong; a switch to a Today task is no detour.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), plan from 09:00.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._guardrail import (
    answer_return,
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
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
async def test_detour_creates_task_in_picked_project_and_asks_return(
    dbos: Any, focus: Focus
) -> None:
    """T-P4-01-04
    At Guardrail with A In progress, "Switched" with a detour {title, project Admin}
    creates the task in Admin (`source` detour, In progress, made by the user), puts A back
    in Today, and fires one `switched` event naming both tasks with the rule "Guardrail ·
    detour" and the return question (the fixed template the master phrases when it is
    online). The focus bar shows the detour as the current task, with the question open.
    """
    admin = await focus.project("Admin")
    day = await guardrail_day(focus)

    answer = await detour(focus, BANK, admin)
    assert answer.status_code == 200, answer.text

    [made] = tasks_titled(focus, BANK)
    assert made["project_id"] == admin
    assert made["source"] == "detour"
    assert made["status"] == "in_progress"
    assert made["created_by"] == f"user:{focus.workspace.user_id}"
    assert made["tainted"] is False
    assert task_row(focus, day.a)["status"] == "today"

    [switched] = focus.events("switched")
    assert switched["detour_task_id"] == made["id"]
    assert switched["return_to_task_id"] == day.a
    assert switched["rule"] == "Guardrail · detour"
    assert switched["level"] == "guardrail"
    assert switched["message"] == "Captured. Back to Write Acme invoice?"
    [payload] = [p for p in focus.payloads("focus.event") if p["kind"] == "switched"]
    assert payload["detour_task_id"] == str(made["id"])
    assert payload["return_to_task_id"] == str(day.a)
    assert payload["rule"] == "Guardrail · detour"

    current = await focus.current()
    assert current["session"]["task_id"] == str(made["id"])
    assert current["detour"]["event_id"] == str(switched["id"])
    assert current["detour"]["detour_task_id"] == str(made["id"])
    assert current["detour"]["detour_title"] == BANK
    assert current["detour"]["return_to_task_id"] == str(day.a)
    assert current["detour"]["return_to_title"] == "Write Acme invoice"
    assert current["guardrail"]["current_task_id"] == str(day.a)


@pytest.mark.req("FR-10.6")
@pytest.mark.wp("P4-01")
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
@pytest.mark.parametrize("decision", ["return", "stay"])
async def test_return_restores_previous_task(dbos: Any, focus: Focus, decision: str) -> None:
    """T-P4-01-05
    Return: the previous task is In progress again and the detour goes to its project's
    Backlog (never Today). Stay: the detour stays In progress and the previous task stays
    in Today. Either way the question is answered once: it is gone from the focus bar, the
    event records the decision, and a second answer is 409 `no_open_detour`.
    """
    admin = await focus.project("Admin")
    day = await guardrail_day(focus)
    assert (await detour(focus, BANK, admin)).status_code == 200
    [made] = tasks_titled(focus, BANK)

    answer = await answer_return(focus, decision, task_row(focus, made["id"])["version"])
    assert answer.status_code == 200, answer.text

    if decision == "return":
        assert task_row(focus, day.a)["status"] == "in_progress"
        assert task_row(focus, made["id"])["status"] == "backlog"
        expected_session = day.a
    else:
        assert task_row(focus, day.a)["status"] == "today"
        assert task_row(focus, made["id"])["status"] == "in_progress"
        expected_session = made["id"]
    current = await focus.current()
    assert current["detour"] is None
    assert current["session"]["task_id"] == str(expected_session)
    [switched] = focus.events("switched")
    assert switched["return_decision"] == decision

    again = await answer_return(focus, decision, task_row(focus, made["id"])["version"])
    assert again.status_code == 409, again.text
    assert again.json()["code"] == "no_open_detour"


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P4-01")
@pytest.mark.xfail(strict=True, reason="spec:P4-01")
async def test_switch_to_today_task_is_not_a_detour(dbos: Any, focus: Focus) -> None:
    """T-P4-01-06
    At Guardrail, "Switched" to a task in Today creates nothing and asks nothing: the
    answer is recorded as P2-15 records it, and starting that task fires the plain
    `switched` event. Below Guardrail a detour is not captured (409
    `detour_needs_guardrail`, nothing created).
    """
    admin = await focus.project("Admin")
    day = await guardrail_day(focus)

    answer = await focus.respond("block_start", "switched", to_task_id=str(day.b))
    assert answer.status_code == 200, answer.text
    await focus.move(day.b, "in_progress")

    assert [p["response"] for p in focus.payloads("focus.responded")] == ["switched"]
    assert tasks_titled(focus, BANK) == []
    assert [t for t in focus.events() if t["detour_task_id"] is not None] == []
    switched = focus.events("switched")
    assert [s["rule"] for s in switched] == ["Guardrail · switched"]
    assert (await focus.current())["detour"] is None

    await focus.level("coach")
    refused = await detour(focus, BANK, admin)
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "detour_needs_guardrail"
    assert tasks_titled(focus, BANK) == []
