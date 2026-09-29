"""The recorded Jev fixtures (P1-01, FR-11.2): every FR-11.4 decision point has one, and
each recording's request is exactly what the builder sends for its stored inputs."""

from __future__ import annotations

from collections import Counter

import pytest

from tumnis.modules.decisions.tests._cases import PINNED_MODEL, POINTS, load_jev_recordings

pytestmark = pytest.mark.contract

FIELDS = {"point", "inputs", "request", "response", "recorded_at", "sdk_version", "notes"}


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
@pytest.mark.xfail(strict=True, reason="spec:P1-01")
def test_recordings_cover_every_point() -> None:
    """T-P1-01-10
    Each of the nine points has at least one recording answered 200; `quick_add_label`
    has a clear `human` and a `hybrid`, and one `project_match` has `unknown` winning. Every
    recording's request equals the wire body of `build_request` over its stored inputs,
    with the pinned model.
    """
    from tumnis.modules.decisions.adapters.jev import wire_body  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    recordings = load_jev_recordings()
    answered = [rec for _, rec in recordings if rec["response"]["status"] == 200]
    assert set(Counter(rec["point"] for rec in answered)) == set(POINTS)

    def winners(point: str, question: str) -> set[str]:
        return {
            rec["response"]["body"]["answers"][question]["choice"]
            for rec in answered
            if rec["point"] == point
        }

    assert {"human", "hybrid"} <= winners("quick_add_label", "label")
    assert "unknown" in winners("project_match", "project")

    for name, rec in recordings:
        assert rec.keys() >= FIELDS, name
        assert rec["request"]["model"] == PINNED_MODEL, name
        req = build_request(DecisionPoint(rec["point"]), rec["inputs"])
        assert rec["request"] == wire_body(req, model=PINNED_MODEL), name
