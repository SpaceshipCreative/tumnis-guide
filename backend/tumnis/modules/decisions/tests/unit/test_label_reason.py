"""The one-line label reason (P1-01, FR-4.1): Jev returns no reason text, so it is built
from the companion Nouls asked with the label."""

from __future__ import annotations

import pytest

LOW = {
    "needs_decision": 0.1,
    "needs_relationship": 0.1,
    "needs_signature": 0.1,
    "needs_presence": 0.1,
    "reserved_judgment": 0.1,
    "specified_verifiable": 0.1,
}

CASES = [
    ("human", {"needs_signature": 0.92, "needs_decision": 0.7}, "Needs a signature"),
    ("human", {"needs_decision": 0.81}, "Needs your decision"),
    ("human", {"needs_relationship": 0.66}, "Depends on a conversation"),
    ("human", {"needs_presence": 0.5}, "Needs someone there in person"),
    ("human", {"reserved_judgment": 0.77}, "A judgment you reserved"),
    ("human", {"needs_decision": 0.49}, "Needs your judgment"),
    ("human", {"specified_verifiable": 0.99}, "Needs your judgment"),
    ("ai", {"specified_verifiable": 0.5}, "Fully specified and verifiable"),
    ("ai", {"specified_verifiable": 0.3, "needs_decision": 0.9}, "Looks automatable"),
    ("hybrid", {"needs_decision": 0.8}, "Part automatable, part needs you: needs your decision"),
    (
        "hybrid",
        {"needs_signature": 0.6, "needs_presence": 0.61},
        "Part automatable, part needs you: needs someone there in person",
    ),
    ("hybrid", {}, "Part automatable, part needs you: needs your judgment"),
]


@pytest.mark.req("FR-4.1")
@pytest.mark.wp("P1-01")
@pytest.mark.parametrize(("label", "signals", "reason"), CASES)
def test_reason_agrees_with_label(label: str, signals: dict[str, float], reason: str) -> None:
    """T-P1-01-14
    A table of companion values gives the expected reason: `human` takes the strongest of
    the first five companions at 0.5 or above, else `Needs your judgment`; `ai` says
    `Fully specified and verifiable` when that companion is at 0.5 or above, else `Looks
    automatable`; `hybrid` names the top human signal. Never empty, one line, at most 80
    characters, and missing companions count as 0.
    """
    from tumnis.modules.decisions.rules import label_reason  # noqa: PLC0415

    companions = {**LOW, **signals}
    got = label_reason(label, companions)  # type: ignore[arg-type]
    assert got == reason
    assert got
    assert "\n" not in got
    assert len(got) <= 80
    assert label_reason(label, signals) == reason  # type: ignore[arg-type]
