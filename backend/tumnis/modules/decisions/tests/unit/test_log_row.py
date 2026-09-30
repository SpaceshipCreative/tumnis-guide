"""Reading a logged decision for labeling (P3-08, FR-11.5): a row whose decision point
the catalogue no longer knows is skipped, so one stale row can't stop the calibration
page or `tumnis decisions eval --from-log`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from tumnis.modules.decisions.api import _log_row
from tumnis.modules.decisions.models import DecisionLog

pytestmark = [pytest.mark.req("FR-11.5"), pytest.mark.wp("P3-08")]


def _logged(decision_point: str) -> DecisionLog:
    return DecisionLog(
        decision_point=decision_point,
        project_id=None,
        subject_type="task",
        subject_id=uuid4(),
        provider="jev",
        model_version="jev-1.13.0",
        input_hash=bytes.fromhex("ab" * 32),
        fields_sent=["title"],
        answer={"retired_question": {"noul": 0.9}},
        confidence=0.8,
        threshold={"t_yes": 0.85, "t_no": 0.15},
        outcome="applied",
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_a_row_for_a_point_the_catalogue_no_longer_knows_is_skipped() -> None:
    assert _log_row(_logged("retired_point")) is None
