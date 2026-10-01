"""Guardrail's block end (P4-01, FR-10.2, FR-10.6): P2-15's `block_end` fires at Guardrail on
the `focus-wake` tick (`POST /v1/test/tick/focus-wake`); the one-task card moves to the next
item only once the current task is Done. P4-01 adds no timer of its own.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture), plan from 09:00.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.focus.tests.integration._focus import TUESDAY, at
from tumnis.modules.focus.tests.integration._guardrail import guardrail_day

if TYPE_CHECKING:
    from tumnis.modules.focus.tests.integration._focus import Focus

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _focus_workflows() -> set[str]:
    from dbos._dbos import _get_or_create_dbos_registry  # noqa: PLC0415  # dbos 3.1.0

    import tumnis.wiring  # noqa: F401, PLC0415  # every module's workflows registered

    names = _get_or_create_dbos_registry().workflow_info_map
    return {name for name in names if name.startswith("focus")}


@pytest.mark.req("FR-10.2")
@pytest.mark.wp("P4-01")
async def test_block_end_reveals_next_at_guardrail(dbos: Any, focus: Focus) -> None:
    """T-P4-01-08
    At Guardrail with A In progress, A's block end (09:30) fires `block_end` and the card
    stays on A (2 more today); once A is Done the card shows B with C next (1 more today).
    The only focus workflows are P2-15's `focus_plan` and `focus_session`.
    """
    day = await guardrail_day(focus)
    current = await focus.current()
    assert current["guardrail"] == {
        "current_task_id": str(day.a),
        "next_task_id": str(day.b),
        "remaining": 2,
    }

    await focus.advance(at(TUESDAY, "09:30"))
    [ended] = focus.events("block_end")
    assert (ended["task_id"], ended["level"]) == (day.a, "guardrail")
    assert (await focus.current())["guardrail"]["current_task_id"] == str(day.a)

    await focus.move(day.a, "done")
    await focus.advance(at(TUESDAY, "09:35"))
    assert (await focus.current())["guardrail"] == {
        "current_task_id": str(day.b),
        "next_task_id": str(day.c),
        "remaining": 1,
    }

    await focus.level("coach")
    assert (await focus.current())["guardrail"] is None
    assert _focus_workflows() == {"focus_plan", "focus_session"}
