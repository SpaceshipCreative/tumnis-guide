"""New review kinds plug in without a schema change (P1-13, FR-6.1, R-03, R-04).

A kind is code: a registered `ReviewKindSpec` with its payload model, its actions and a
payload model per action that takes one. Questions, approvals, results and proposals (P2,
P3) arrive this way; this test registers a question-like kind of its own and walks the
three things the queue does with it (add, list, decide) through the same validation the
database path uses, against the columns the migrations already made.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

KIND = "test_future_question"
NOW = datetime(2026, 3, 9, 12, tzinfo=UTC)
MODULES = Path(__file__).resolve().parents[3]  # backend/tumnis/modules


class QuestionPayload(BaseModel):
    prompt: str
    options: list[str] = []


class AnswerPayload(BaseModel):
    text: str


def _modules() -> tuple[Any, Any, Any]:
    """tasks' api, review and rules modules (typed loosely: the spec names arrive with the
    code)."""
    from tumnis.modules.tasks import api, review, rules  # noqa: PLC0415

    return api, review, rules


def _spec() -> Any:
    api = _modules()[0]
    return api.ReviewKindSpec(
        kind=KIND,
        owner_module="testmod",
        payload_schema=QuestionPayload,
        actions=("answer", "snooze"),
        impact_scope="workspace",
        action_payloads={"answer": AnswerPayload},
    )


@pytest.mark.req("FR-6.1")
@pytest.mark.wp("P1-13")
@pytest.mark.xfail(strict=True, reason="spec:P1-13")
def test_new_kind_needs_no_migration() -> None:
    """T-P1-13-11
    Registering a test kind with its payload model allows add, list and decide against
    the migrated schema with no new migration file: the payload validates to the JSON the
    `payload` column stores, a row of the kind reads back as a queue item, `answer` (its
    primary action) validates its own payload, an action it does not offer is 422
    `action_not_allowed`, and no revision anywhere names the kind.
    """
    from pydantic import ValidationError  # noqa: PLC0415

    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.modules.tasks.models import ReviewItem  # noqa: PLC0415

    api, review, rules = _modules()
    spec = _spec()
    if KIND not in api.review_kinds():
        api.register_review_kind(spec)
    spec = api.review_kinds()[KIND]

    # add: the kind's payload model, dumped to what the jsonb column holds
    stored = review.validate_payload(KIND, {"prompt": "Which colour?", "options": ["Navy"]})
    assert stored == {"prompt": "Which colour?", "options": ["Navy"]}
    with pytest.raises(ValidationError):
        review.validate_payload(KIND, {"options": []})

    # list: a row of the new kind, with only the columns the migrations made
    columns = set(ReviewItem.__table__.columns.keys())
    row = {
        "id": uuid.uuid4(),
        "kind": KIND,
        "project_id": None,
        "target_type": "run",
        "target_id": uuid.uuid4(),
        "payload": stored,
        "blocking_impact": "2.5",
        "jev_factor": None,
        "snoozed_until": None,
        "decided_at": None,
        "decision": None,
        "version": 1,
        "created_at": NOW,
        "updated_at": NOW,
    }
    assert set(row) <= columns
    item = api.ReviewItemOut.from_row(row)
    assert item.kind == KIND
    assert item.actions == ["answer", "snooze"]
    assert item.primary_action == "answer"
    assert rules.primary_action(spec) == "answer"
    assert re.fullmatch(r"^[a-z][a-z0-9_]{2,40}$", KIND)

    # decide: the action's own payload model; an action the kind lacks is refused
    assert review.validate_decision(
        spec, "answer", {"text": "Navy"}, snooze_until=None, now=NOW
    ) == {"text": "Navy"}
    until = NOW + timedelta(hours=1)
    assert review.validate_decision(spec, "snooze", None, snooze_until=until, now=NOW) is None
    for action in ("accept", "approve", "edit"):
        with pytest.raises(ProblemError) as refused:
            review.validate_decision(spec, action, None, snooze_until=None, now=NOW)
        assert (refused.value.status, refused.value.code) == (422, "action_not_allowed")
    with pytest.raises(ProblemError) as bad:
        review.validate_decision(spec, "answer", {"colour": 3}, snooze_until=None, now=NOW)
    assert (bad.value.status, bad.value.code) == (422, "invalid_review_payload")

    # no migration of any module mentions the kind
    revisions = list(MODULES.glob("*/migrations/*.py"))
    assert revisions
    assert not [path for path in revisions if KIND in path.read_text()]
