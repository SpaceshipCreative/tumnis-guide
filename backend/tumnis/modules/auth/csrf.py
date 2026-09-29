"""Cookies, the CSRF token and signed short-lived tokens (P0-13, SEC-1, R-18).

- Session cookie `__Host-tumnis_session`: 32 random bytes, HttpOnly, Secure, SameSite=Lax,
  Path=/ (the `__Host-` prefix forbids a Domain). The database keeps only
  HMAC-SHA256(pepper, token).
- CSRF cookie `__Host-tumnis_csrf`: HMAC-SHA256(csrf_key, session_id), readable by the app
  (not HttpOnly); the frontend sends it back as `X-CSRF-Token` on every write, and
  `TumnisRoute` compares the two with `hmac.compare_digest`. `csrf_key` is derived from
  the pepper with HKDF (info `tumnis:csrf`), so the token cannot be forged without it.
- Signed tokens (the pre-auth token between the password and TOTP steps, the setup token
  between setup and its confirmation): base64url JSON claims with an expiry, then an
  HMAC under a per-purpose key (HKDF info `tumnis:<purpose>`).

Tokens never reach logs: the redaction filter covers these names (P0-16, P0-27).
"""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta
from typing import Any, Final
from uuid import UUID

from starlette.responses import Response

from tumnis.core import crypto

SESSION_COOKIE: Final = "__Host-tumnis_session"
CSRF_COOKIE: Final = "__Host-tumnis_csrf"
TOKEN_BYTES: Final = 32
# The server ends idle sessions (30 days); the cookie itself may live as long as browsers
# allow, so a session in use is never dropped by the browser first.
COOKIE_MAX_AGE_S: Final = int(timedelta(days=400).total_seconds())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _pepper() -> bytes:
    peppers = crypto.peppers()
    return peppers.keys[peppers.active]


def new_session_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def session_token_hmac(token: str) -> bytes:
    """What the sessions row stores: HMAC-SHA256(pepper, token)."""
    return hmac.new(_pepper(), token.encode(), hashlib.sha256).digest()


def csrf_token(session_id: UUID) -> str:
    key = crypto.derive_key(_pepper(), "tumnis:csrf")
    return _b64(hmac.new(key, session_id.bytes, hashlib.sha256).digest())


def set_session_cookies(response: Response, token: str, csrf: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=COOKIE_MAX_AGE_S,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=COOKIE_MAX_AGE_S,
        path="/",
        secure=True,
        httponly=False,
        samesite="lax",
    )


def clear_session_cookies(response: Response) -> None:
    for name, httponly in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=True, httponly=httponly, samesite="lax")


# --- Signed short-lived tokens ----------------------------------------------------------


def sign(purpose: str, claims: dict[str, Any], *, expires_at: datetime) -> str:
    body = json.dumps(
        {**claims, "p": purpose, "exp": int(expires_at.timestamp())},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    key = crypto.derive_key(_pepper(), f"tumnis:{purpose}")
    return f"{_b64(body)}.{_b64(hmac.new(key, body, hashlib.sha256).digest())}"


def unsign(purpose: str, token: str, *, now: datetime) -> dict[str, Any] | None:
    """The claims of a token signed for `purpose` that has not expired, else None."""
    try:
        body_part, mac_part = token.split(".", 1)
        body, mac = _unb64(body_part), _unb64(mac_part)
    except (ValueError, binascii.Error):
        return None
    key = crypto.derive_key(_pepper(), f"tumnis:{purpose}")
    if not hmac.compare_digest(mac, hmac.new(key, body, hashlib.sha256).digest()):
        return None
    try:
        claims = json.loads(body)
    except ValueError:
        return None
    if not isinstance(claims, dict) or claims.get("p") != purpose:
        return None
    expires = claims.get("exp")
    if not isinstance(expires, int) or now.timestamp() >= expires:
        return None
    return claims
