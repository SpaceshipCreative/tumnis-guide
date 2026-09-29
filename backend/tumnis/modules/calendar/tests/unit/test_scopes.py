"""Only read-only Google scopes are ever requested (P1-09, Data flow rule 1)."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest


@pytest.mark.req("Data flow rule 1")
@pytest.mark.wp("P1-09")
@pytest.mark.xfail(strict=True, reason="spec:P1-09")
def test_only_readonly_scopes_requested() -> None:
    """T-P1-09-07
    The consent URL asks for exactly READONLY_SCOPES, each ending in `.readonly`, with
    offline access, a forced consent screen and a PKCE S256 challenge.
    """
    from tumnis.modules.calendar.api import consent_url  # noqa: PLC0415
    from tumnis.modules.calendar.rules import READONLY_SCOPES  # noqa: PLC0415

    url = consent_url(
        client_id="client-123.apps.example.com",
        redirect_uri="https://tumnis.example.com/v1/calendar/oauth/callback",
        state="state-abc",
        code_challenge="challenge-xyz",
    )
    parts = urlsplit(url)
    query = parse_qs(parts.query)

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    scopes = set(query["scope"][0].split(" "))
    assert scopes == set(READONLY_SCOPES)
    assert scopes, "at least one scope"
    assert all(scope.endswith(".readonly") for scope in scopes)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["response_type"] == ["code"]
    assert query["code_challenge"] == ["challenge-xyz"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["state-abc"]
    assert query["client_id"] == ["client-123.apps.example.com"]
    assert query["redirect_uri"] == ["https://tumnis.example.com/v1/calendar/oauth/callback"]
