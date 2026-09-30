"""The recorded GitHub answers (P2-13, FR-12.1): the real read-only client reads each
recorded pull request with its commit statuses, check runs and reviews, and the module
maps them to the Artifact a task card shows (state, checks and review)."""

from __future__ import annotations

import pytest

from tumnis.modules.github.tests.replay import T0, TOKEN, Replay, load, recording_names

pytestmark = pytest.mark.contract


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
@pytest.mark.parametrize("recording", recording_names())
async def test_recordings_map_to_artifacts(recording: str) -> None:
    """T-P2-13-05
    Given a recording (open with green checks and an approval, open with a failed check
    run, merged, closed unmerged, pending checks, changes requested, statuses only, 304 not
    modified), the real client over the replayed answers makes the recorded GETs (pull,
    combined status and check runs of the head commit, reviews) with the read-only token,
    the API version and, on a second round, `If-None-Match`; a second round answered 304
    changes nothing and counts four not-modified answers. The pull request maps to the
    recorded Artifact: external id, canonical URL, state and `checks.pr_status`.
    """
    from tumnis.modules.github.api import artifact_record, fetch_pull_request  # noqa: PLC0415
    from tumnis.modules.github.rules import PrRef  # noqa: PLC0415
    from tumnis.modules.github.tests.replay import recorded_api  # noqa: PLC0415

    rec = load(recording)
    assert rec["notes"].startswith("Scrubbed:")
    replay = Replay(rec)
    subject = recorded_api(replay)
    ref = PrRef(**rec["pull"])

    fetched = await fetch_pull_request(subject, ref, previous=None)
    assert (fetched.requests, fetched.not_modified) == (4, 0)
    if rec["rounds"] == 2:
        again = await fetch_pull_request(subject, ref, previous=fetched.snapshot)
        assert (again.requests, again.not_modified) == (4, 4)
        assert again.snapshot == fetched.snapshot

    exchanges = rec["exchanges"]
    assert [(r.method, r.url.path, dict(r.url.params)) for r in replay.requests] == [
        (ex["method"], ex["path"], ex["query"]) for ex in exchanges
    ]
    for request, ex in zip(replay.requests, exchanges, strict=True):
        assert request.headers.get("if-none-match") == ex["if_none_match"]
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.headers["accept"] == "application/vnd.github+json"
        assert request.headers["x-github-api-version"] == "2022-11-28"
        assert request.headers["host"] == "api.github.com"

    record = artifact_record(ref, fetched.snapshot, fetched_at=T0)
    expected = rec["expected"]
    assert record.kind == "pull_request"
    assert (record.external_id, record.url, record.provider_url) == (
        expected["external_id"],
        expected["url"],
        expected["url"],
    )
    assert record.state == expected["state"]
    assert record.checks == {"pr_status": expected["pr_status"]}
