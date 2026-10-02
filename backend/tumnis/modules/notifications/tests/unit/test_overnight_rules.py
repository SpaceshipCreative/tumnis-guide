"""The overnight batch rules (P4-04, J7, FR-8.4): an unattended night's review items are held
for the morning release, which sends one summary naming the results and what needs the
person. Pure functions; no clock, no network."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tumnis.modules.notifications import rules

RELEASE_AT = datetime(2026, 3, 10, 12, 45, tzinfo=UTC)


@pytest.mark.req("J7", "FR-8.4")
@pytest.mark.wp("P4-04")
def test_overnight_rows_wait_for_their_release_time() -> None:
    assert not rules.overnight_due(RELEASE_AT, RELEASE_AT - timedelta(minutes=5))
    assert rules.overnight_due(RELEASE_AT, RELEASE_AT)
    assert rules.overnight_due(RELEASE_AT, RELEASE_AT + timedelta(hours=1))
    assert rules.overnight_due(None, RELEASE_AT)  # no release time: the first release takes it


@pytest.mark.req("J7", "FR-8.4")
@pytest.mark.wp("P4-04")
def test_an_overnight_row_reaches_only_the_app_until_released() -> None:
    assert rules.channels_now("overnight") == ("in_app",)


@pytest.mark.req("J7", "FR-8.4")
@pytest.mark.wp("P4-04")
@pytest.mark.parametrize(
    ("results", "others", "title"),
    [
        (1, 0, "1 result from overnight"),
        (2, 0, "2 results from overnight"),
        (1, 1, "1 result from overnight, 1 needs you"),
        (3, 2, "3 results from overnight, 2 need you"),
        (0, 2, "0 results from overnight, 2 need you"),
    ],
)
def test_the_morning_summary_names_results_and_what_needs_you(
    results: int, others: int, title: str
) -> None:
    payload = rules.overnight_payload(results, others)
    assert payload.title == title
    assert payload.kind == "batch"
    assert payload.url == "/review"
    assert payload.tag == "overnight"
