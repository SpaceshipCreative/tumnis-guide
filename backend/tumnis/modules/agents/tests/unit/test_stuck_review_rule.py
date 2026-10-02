"""A stuck run's result, decided (Scott decision 73, FR-10.5): accepting the report marks
the stuck step done; rejecting it reopens the step for the person. Other decisions (a
snooze) leave the step as it is."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-10.5")
@pytest.mark.wp("P4-02")
@pytest.mark.xfail(strict=True, reason="spec:FIX-stuck-review")
@pytest.mark.parametrize(
    ("decision", "state"),
    [("accept", "done"), ("reject", "reopened"), ("snooze", None), ("edit", None)],
)
def test_stuck_step_after_decision(decision: str, state: str | None) -> None:
    from tumnis.modules.agents import rules  # noqa: PLC0415

    stuck_step_after = rules.stuck_step_after  # type: ignore[attr-defined]

    assert stuck_step_after(decision) == state
