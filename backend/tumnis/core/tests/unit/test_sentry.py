"""GlitchTip through the Sentry SDK: no bodies, cookies or tokens leave the process (P0-27,
REL-5, SEC-6)."""

from __future__ import annotations

import json
from typing import Any

import pytest


def _event() -> dict[str, Any]:
    return {
        "message": "upstream failed for Bearer tok-in-message",
        "logentry": {"message": "retry with password=pw-in-logentry", "params": []},
        "request": {
            "url": "https://tumnis.lan/v1/tasks",
            "method": "POST",
            "data": {"title": "request body title"},
            "cookies": {"__Host-tumnis_session": "session-cookie-value"},
            "headers": {
                "Authorization": "Bearer tok-in-header",
                "Cookie": "__Host-tumnis_csrf=csrf-cookie-value",
                "X-CSRF-Token": "csrf-header-value",
                "User-Agent": "pytest-agent",
            },
            "query_string": "api_key=key-in-query",
        },
        "extra": {"api_key": "key-in-extra", "body": "extra body text", "note": "kept note"},
        "exception": {
            "values": [
                {
                    "type": "ValueError",
                    "value": "bad key tmn_abcdefghijkl_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
                }
            ]
        },
        "breadcrumbs": {
            "values": [{"message": "sent", "data": {"body": "breadcrumb body text"}}],
        },
    }


@pytest.mark.req("REL-5", "SEC-6")
@pytest.mark.wp("P0-27")
@pytest.mark.xfail(strict=True, reason="spec:P0-27")
def test_before_send_strips_bodies_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-27-10
    `before_send` drops the request body and cookies, removes the Authorization, Cookie and
    CSRF headers, and redacts tokens and bodies anywhere else in the event; what is left is
    still useful (URL, method, user agent, exception type). `init_sentry` stays off without a
    DSN and, with one, sends no PII, no traces and no request bodies.
    """
    import sentry_sdk  # noqa: PLC0415

    from tumnis.core import errors_sentry  # noqa: PLC0415

    out = errors_sentry.before_send(_event(), {})
    assert out is not None
    text = json.dumps(out)
    for secret in (
        "tok-in-message",
        "pw-in-logentry",
        "request body title",
        "session-cookie-value",
        "tok-in-header",
        "csrf-cookie-value",
        "csrf-header-value",
        "key-in-query",
        "key-in-extra",
        "extra body text",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
        "breadcrumb body text",
    ):
        assert secret not in text
    assert out["request"]["url"] == "https://tumnis.lan/v1/tasks"
    assert out["request"]["method"] == "POST"
    assert out["request"]["headers"]["User-Agent"] == "pytest-agent"
    assert out["extra"]["note"] == "kept note"
    assert out["exception"]["values"][0]["type"] == "ValueError"

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: calls.append(kwargs))
    assert errors_sentry.init_sentry(None, environment="prod") is False
    assert calls == []
    assert errors_sentry.init_sentry("https://k@glitchtip.lan/1", environment="prod") is True
    (options,) = calls
    assert options["dsn"] == "https://k@glitchtip.lan/1"
    assert options["send_default_pii"] is False
    assert options["traces_sample_rate"] == 0
    assert options["max_request_body_size"] == "never"
    assert options["include_local_variables"] is False
    assert options["before_send"] is errors_sentry.before_send
