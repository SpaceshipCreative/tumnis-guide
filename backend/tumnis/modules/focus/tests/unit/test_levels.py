"""Guardrail on top of P2-15's levels (P4-01, FR-10.2, FR-10.6): detour capture is a
Guardrail behavior only, and the level by event table stays P2-15's (imported, never
copied). The rules are imported inside the test, so this file collects before they exist.
"""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-10.2", "FR-10.6")
@pytest.mark.wp("P4-01")
def test_detour_capture_only_at_guardrail() -> None:
    """T-P4-01-01
    `captures_detour` is true at Guardrail and nowhere else; Guardrail's events are still
    exactly Coach's plus `block_end` (P2-15's `LEVEL_EVENTS`, unchanged).
    """
    from tumnis.modules.focus.rules import LEVEL_EVENTS, LEVELS, captures_detour  # noqa: PLC0415

    assert {level: captures_detour(level) for level in LEVELS} == {
        "quiet": False,
        "nudge": False,
        "coach": False,
        "guardrail": True,
    }
    assert LEVEL_EVENTS["guardrail"] == LEVEL_EVENTS["coach"] | {"block_end"}
    assert "block_end" not in LEVEL_EVENTS["coach"]
