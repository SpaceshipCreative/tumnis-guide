"""The github module's pure rules (P2-13, FR-12.1, SEC-5): pull request URLs, the combined
check and review states a task card shows, and the webhook signature check."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

import pytest


def _status(state: str, context: str = "ci") -> Any:
    from tumnis.modules.github.rules import StatusView  # noqa: PLC0415

    return StatusView(context=context, state=state)


def _run(status: str, conclusion: str | None = None, name: str = "build") -> Any:
    from tumnis.modules.github.rules import CheckRunView  # noqa: PLC0415

    return CheckRunView(name=name, status=status, conclusion=conclusion)


def _review(login: str, state: str, at: str) -> Any:
    from tumnis.modules.github.rules import ReviewView  # noqa: PLC0415

    return ReviewView.model_validate({"user": {"login": login}, "state": state, "submitted_at": at})


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
def test_parse_pr_url() -> None:
    """T-P2-13-10
    github.com pull request URLs parse to (owner, repo, number), with or without a trailing
    path, query or fragment; GitHub Enterprise and look-alike hosts, other schemes, issue
    URLs and malformed numbers are refused (None).
    """
    from tumnis.modules.github.rules import parse_pr_url  # noqa: PLC0415

    ok = ("acme-example", "site", 42)
    for url in (
        "https://github.com/acme-example/site/pull/42",
        "https://github.com/acme-example/site/pull/42/",
        "https://github.com/acme-example/site/pull/42/files",
        "https://github.com/acme-example/site/pull/42/commits/0a1b2c3d",
        "https://github.com/acme-example/site/pull/42/checks?check_run_id=7",
        "https://github.com/acme-example/site/pull/42#issuecomment-1",
        "https://www.github.com/acme-example/site/pull/42",
        "  https://github.com/acme-example/site/pull/42  ",
    ):
        assert parse_pr_url(url) == ok, url
    assert parse_pr_url("https://github.com/Acme-Example/site.web_2/pull/7") == (
        "Acme-Example",
        "site.web_2",
        7,
    )
    for url in (
        "https://github.example.com/acme-example/site/pull/42",  # GitHub Enterprise
        "https://ghe.acme.example.org/acme-example/site/pull/42",
        "https://github.com.example.org/acme-example/site/pull/42",
        "https://gist.github.com/acme-example/site/pull/42",
        "https://api.github.com/repos/acme-example/site/pulls/42",
        "https://user@github.com/acme-example/site/pull/42",
        "https://github.com:8443/acme-example/site/pull/42",
        "http://github.com/acme-example/site/pull/42",
        "ftp://github.com/acme-example/site/pull/42",
        "github.com/acme-example/site/pull/42",
        "https://github.com/acme-example/site/issues/42",
        "https://github.com/acme-example/site/pulls/42",
        "https://github.com/acme-example/site/pull/0",
        "https://github.com/acme-example/site/pull/-1",
        "https://github.com/acme-example/site/pull/4x2",
        "https://github.com/acme-example/site/pull/",
        "https://github.com/acme-example/pull/42",
        "https://github.com/acme example/site/pull/42",
        "https://github.com/../site/pull/42",
        "",
    ):
        assert parse_pr_url(url) is None, url


@pytest.mark.req("FR-12.1")
@pytest.mark.wp("P2-13")
def test_combine_checks_and_review_tables() -> None:
    """T-P2-13-06
    `combine_checks` merges commit statuses and check runs: red if any failure, error,
    cancelled, timed_out or action_required; pending if any queued or in progress and none
    red; green if at least one and all success, neutral or skipped; none if empty.
    `review_state` takes each reviewer's latest review; changes requested wins over approved.
    """
    from tumnis.modules.github.rules import combine_checks, review_state  # noqa: PLC0415

    checks: list[tuple[list[Any], list[Any], str]] = [
        ([], [], "none"),
        ([_status("success")], [], "green"),
        ([], [_run("completed", "success")], "green"),
        ([], [_run("completed", "neutral"), _run("completed", "skipped")], "green"),
        ([_status("success")], [_run("completed", "success")], "green"),
        ([_status("failure")], [_run("completed", "success")], "red"),
        ([_status("error")], [], "red"),
        ([], [_run("completed", "failure")], "red"),
        ([], [_run("completed", "cancelled")], "red"),
        ([], [_run("completed", "timed_out")], "red"),
        ([], [_run("completed", "action_required")], "red"),
        ([_status("pending")], [], "pending"),
        ([], [_run("queued")], "pending"),
        ([], [_run("in_progress")], "pending"),
        ([_status("success")], [_run("in_progress")], "pending"),
        ([_status("pending")], [_run("completed", "failure")], "red"),
        ([], [_run("in_progress"), _run("completed", "timed_out")], "red"),
    ]
    for statuses, runs, expected in checks:
        assert combine_checks(statuses, runs) == expected, (statuses, runs)

    reviews: list[tuple[list[Any], str]] = [
        ([], "none"),
        ([_review("ana", "APPROVED", "2026-03-09T10:00:00Z")], "approved"),
        ([_review("ana", "CHANGES_REQUESTED", "2026-03-09T10:00:00Z")], "changes_requested"),
        (
            [
                _review("ana", "APPROVED", "2026-03-09T10:00:00Z"),
                _review("ben", "CHANGES_REQUESTED", "2026-03-09T11:00:00Z"),
            ],
            "changes_requested",
        ),
        (
            [
                _review("ana", "CHANGES_REQUESTED", "2026-03-09T10:00:00Z"),
                _review("ana", "APPROVED", "2026-03-09T11:00:00Z"),
            ],
            "approved",
        ),
        (
            [
                _review("ana", "APPROVED", "2026-03-09T10:00:00Z"),
                _review("ana", "COMMENTED", "2026-03-09T11:00:00Z"),
            ],
            "approved",
        ),
        (
            [
                _review("ana", "CHANGES_REQUESTED", "2026-03-09T10:00:00Z"),
                _review("ana", "DISMISSED", "2026-03-09T11:00:00Z"),
            ],
            "review_required",
        ),
        ([_review("ana", "COMMENTED", "2026-03-09T10:00:00Z")], "review_required"),
    ]
    for given, expected in reviews:
        assert review_state(given) == expected, given


def _sign(secret: bytes, body: bytes) -> str:
    return "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P2-13")
def test_verify_signature() -> None:
    """T-P2-13-03
    `X-Hub-Signature-256` is `sha256=<hex>` of HMAC-SHA256 over the raw body with the
    webhook secret: the right one is accepted; a wrong secret, a missing or malformed header,
    an altered body, another algorithm and an empty secret are refused.
    """
    from tumnis.modules.github.signature import verify_signature  # noqa: PLC0415

    secret = b"not-a-real-webhook-secret"
    body = b'{"action":"synchronize","number":42}'
    good = _sign(secret, body)

    assert verify_signature(secret, body, good) is True
    assert verify_signature(secret, body, good.upper().replace("SHA256=", "sha256=")) is True
    assert verify_signature(b"another-secret", body, good) is False
    assert verify_signature(secret, body, None) is False
    assert verify_signature(secret, body, "") is False
    assert verify_signature(secret, body, good.removeprefix("sha256=")) is False
    assert verify_signature(secret, body, "sha256=zz" + good[9:]) is False
    assert verify_signature(secret, body + b" ", good) is False
    assert verify_signature(secret, body.replace(b"42", b"43"), good) is False
    sha1 = "sha1=" + hmac.new(secret, body, hashlib.sha1).hexdigest()
    assert verify_signature(secret, body, sha1) is False
    assert verify_signature(b"", body, _sign(b"", body)) is False
