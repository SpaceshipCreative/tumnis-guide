"""Logs never contain tokens, bodies or prompts (P0-16, SEC-6).

The redaction processor is P0-27's `tumnis.core.logging.redact`; these tests drive it
through the real processor chain and JSON renderer (`configure_logging` on an in-memory
stream), so what is checked is the line that would reach stdout.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

if TYPE_CHECKING:
    from tests.fixtures import JsonLogs

URL_SAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
BASE32 = "abcdefghijklmnopqrstuvwxyz234567"
SENSITIVE_KEYS = (
    "token",
    "access_token",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "authorization",
    "Cookie",
    "set_cookie",
    "api_key",
    "x-api-key",
    "hmac",
    "pepper",
    "csrf_token",
)
DROPPED_KEYS = ("body", "body_text", "prompt", "messages", "packet")


def _distinct(secret: str) -> bool:
    """Each half holds an upper-case letter and a digit, so neither half can turn up in a
    key name or `[redacted]` by chance."""
    half = len(secret) // 2
    return all(
        any(c.isupper() for c in part) and any(c.isdigit() for c in part)
        for part in (secret[:half], secret[half:])
    )


# Random URL-safe secrets of 16 to 64 characters.
secrets_ = st.text(URL_SAFE, min_size=16, max_size=64).filter(_distinct)
tumnis_tokens = st.builds(
    lambda kind, public, tail: f"tm{kind}_{public}_{tail}",
    st.sampled_from("ntd"),
    st.text(BASE32, min_size=12, max_size=12),
    st.text(URL_SAFE, min_size=43, max_size=43),
)


def _leaks(secret: str, line: str) -> bool:
    """The secret, or either half of it, appears in the line."""
    half = len(secret) // 2
    return secret in line or secret[:half] in line or secret[half:] in line


def _new_lines(logs: JsonLogs, seen: int) -> tuple[str, int]:
    text = logs.text()
    return text[seen:], len(text)


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-16")
@settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    secret=secrets_,
    token=tumnis_tokens,
    key=st.sampled_from(SENSITIVE_KEYS),
    framing=st.sampled_from(
        ("Bearer {}", "bearer  {}", "password={}", "api_key={}&x=1", "token={}")
    ),
)
def test_random_secrets_never_reach_the_log_line(
    capture_json_logs: JsonLogs, secret: str, token: str, key: str, framing: str
) -> None:
    """T-P0-16-12
    Random URL-safe secrets (16 to 64 characters) under sensitive keys, in nested dicts and
    lists, and inside free text as a `tmn_...` token, `Bearer ...` or `password=...`, from
    structlog and from a stdlib logger: the rendered JSON line contains none of them.
    """
    import structlog  # noqa: PLC0415

    seen = len(capture_json_logs.text())
    free_text = framing.format(secret)
    structlog.get_logger("tumnis.test").info(
        f"calling out with {free_text} and {token}",
        **{key: secret},
        nested={"level": [{key: secret}, {"note": free_text}], "ok": "kept"},
        items=[free_text, f"key {token} used", {"deeper": {key: [secret]}}],
    )
    logging.getLogger("uvicorn.error").warning(
        "retry after %s", free_text, extra={key: secret, "context": {"note": token}}
    )

    lines, _ = _new_lines(capture_json_logs, seen)
    assert len([line for line in lines.splitlines() if line.strip()]) == 2
    for value in (secret, token):
        assert not _leaks(value, lines), (value, lines)
    assert "kept" in lines


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        found = {str(k) for k in value}
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value)) if value else set()
    return set()


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-16")
def test_bodies_and_prompts_are_dropped(capture_json_logs: JsonLogs) -> None:
    """T-P0-16-13
    Keys `body`, `body_text`, `prompt`, `messages` and `packet` are absent from the rendered
    line at any depth, with their contents, from structlog and from a stdlib logger.
    """
    import structlog  # noqa: PLC0415

    payload = {name: f"CONTENT-OF-{name}" for name in DROPPED_KEYS}
    structlog.get_logger("tumnis.test").info(
        "request handled",
        **payload,
        nested={"inner": [dict(payload), {"deeper": dict(payload)}], "status": 200},
    )
    logging.getLogger("uvicorn.error").info("stdlib line", extra=dict(payload))

    lines = capture_json_logs.lines()
    assert len(lines) == 2
    for line in lines:
        assert not (_keys(line) & set(DROPPED_KEYS)), line
    text = json.dumps(lines)
    assert "CONTENT-OF-" not in text
    assert lines[0]["nested"]["status"] == 200


@pytest.mark.req("SEC-6")
@pytest.mark.wp("P0-16")
def test_access_log_omits_query_strings(capture_json_logs: JsonLogs) -> None:
    """uvicorn's access line keeps the method, path and status but never the query
    string, where some providers put tokens."""
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d',
        "198.51.100.7:5000",
        "GET",
        "/v1/oauth/callback?code=QUERY-SECRET-123&state=xyz",
        "1.1",
        200,
    )
    (line,) = capture_json_logs.lines()
    assert "QUERY-SECRET-123" not in json.dumps(line)
    assert '"GET /v1/oauth/callback HTTP/1.1" 200' in line["event"]
