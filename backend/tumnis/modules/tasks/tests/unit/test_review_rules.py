"""The review queue's other pure rules (P1-13): the nearest due date Jev's blocking-impact
question reads, the Enter key's primary action, and the payload checks of a decision."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import BaseModel

from tumnis.core.errors import ProblemError
from tumnis.modules.tasks import rules
from tumnis.modules.tasks.review import ReviewKindSpec, validate_decision

NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
R, C1, C2, S = (uuid.uuid4() for _ in range(4))
GRAPH = rules.TaskGraph(
    tasks=(
        rules.GraphTask(R, None, rules.Status.TODAY, rules.Label.HUMAN, 30, date(2026, 3, 20)),
        rules.GraphTask(C1, R, rules.Status.BACKLOG, rules.Label.AI, None, date(2026, 3, 12)),
        rules.GraphTask(C2, R, rules.Status.DONE, rules.Label.HUMAN, 10, date(2026, 3, 10)),
        rules.GraphTask(S, None, rules.Status.BACKLOG, rules.Label.HUMAN, 20, None),
    )
)


class _Payload(BaseModel):
    note: str


def _spec(*actions: str) -> ReviewKindSpec:
    return ReviewKindSpec(
        kind="test_rules_kind",
        owner_module="test",
        payload_schema=_Payload,
        actions=actions,
        impact_scope="task",
        action_payloads={"edit": _Payload},
    )


@pytest.mark.wp("P1-13")
@pytest.mark.parametrize(
    ("target", "scope", "expected"),
    [
        (R, "task", date(2026, 3, 12)),  # the Done child's earlier date does not count
        (S, "task", None),
        (None, "task", None),
        (None, "project", date(2026, 3, 12)),
    ],
)
def test_nearest_due(target: uuid.UUID | None, scope: rules.ImpactScope, expected: date) -> None:
    assert rules.nearest_due(target, scope, GRAPH) == expected


@pytest.mark.wp("P1-13")
def test_primary_action_falls_back_to_the_first() -> None:
    assert rules.primary_action(_spec("reject", "approve")) == "approve"
    assert rules.primary_action(_spec("snooze", "deny")) == "snooze"


@pytest.mark.wp("P1-13")
def test_decision_payload_checks() -> None:
    spec = _spec("edit", "reject", "snooze")
    assert validate_decision(spec, "edit", {"note": "x"}, snooze_until=None, now=NOW) == {
        "note": "x"
    }
    assert validate_decision(spec, "reject", None, snooze_until=None, now=NOW) is None
    for action, payload, until in (
        ("reject", {"note": "x"}, None),  # takes no payload
        ("edit", None, None),  # needs one
        ("snooze", None, NOW),  # not in the future
    ):
        with pytest.raises(ProblemError) as refused:
            validate_decision(spec, action, payload, snooze_until=until, now=NOW)
        assert refused.value.status == 422
    ok = validate_decision(spec, "snooze", None, snooze_until=NOW + timedelta(minutes=1), now=NOW)
    assert ok is None
