"""Golden outbound payloads (P1-01, Data flow rule 6): what each decision point sends is
fixed and reviewed; changing a snapshot is a spec change."""

from __future__ import annotations

import json

import pytest

from tumnis.modules.decisions.tests._cases import POINTS, SNAPSHOTS, stored_inputs

pytestmark = pytest.mark.contract


def serialize(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-01")
@pytest.mark.parametrize("point", sorted(POINTS))
def test_payload_matches_snapshot(point: str) -> None:
    """T-P1-01-07
    The request built from `<point>.inputs.json`, dumped with `model_dump(mode="json")`
    and serialized with sorted keys, equals `<point>.payload.json`.
    """
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    req = build_request(DecisionPoint(point), stored_inputs(point))
    golden = (SNAPSHOTS / f"{point}.payload.json").read_text()
    assert serialize(req.model_dump(mode="json")) == golden
