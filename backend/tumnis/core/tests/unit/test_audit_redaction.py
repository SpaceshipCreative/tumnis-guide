"""`redact_details` keeps tokens, secrets and bodies out of the audit log (P0-15)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

# Keys the redaction must recognise, in any case and inside longer names.
SENSITIVE = (
    "token",
    "secret",
    "password",
    "passwd",
    "hmac",
    "key",
    "authorization",
    "cookie",
    "body",
    "prompt",
    "content",
    "text",
)
# Keys that match none of them.
PLAIN = ("id", "name", "count", "mode", "status", "scope", "route", "kind")
MARK = "SECRETVALUE"
TOKEN_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

sensitive_keys = st.builds(
    lambda prefix, word, suffix, upper: prefix + (word.upper() if upper else word) + suffix,
    st.sampled_from(("", "api_", "x-", "session_", "raw")),
    st.sampled_from(SENSITIVE),
    st.sampled_from(("", "_value", "s", "Id")),
    st.booleans(),
)
secrets_ = st.builds(lambda tail: f"{MARK}-{tail}", st.text(TOKEN_CHARS, min_size=4, max_size=40))
credentials = st.builds(
    lambda prefix, tail, framing: framing.format(f"{prefix}{tail}"),
    st.sampled_from(("tmn_", "tmt_", "tmd_")),
    st.text(TOKEN_CHARS, min_size=8, max_size=40),
    st.sampled_from(("{}", "Bearer {}", "key={} was used")),
)
plain_values = st.one_of(
    st.integers(), st.booleans(), st.none(), st.text("abcdefghij ", max_size=20)
)


def _tree(depth: int = 3) -> st.SearchStrategy[dict[str, Any]]:
    leaf = st.one_of(plain_values, credentials)
    child = st.deferred(lambda: _tree(depth - 1)) if depth > 0 else leaf
    value = st.one_of(leaf, child, st.lists(st.one_of(leaf, child), max_size=3))
    return st.dictionaries(
        st.one_of(st.sampled_from(PLAIN), sensitive_keys),
        st.one_of(value, secrets_),
        max_size=5,
    ).map(_secrets_only_under_sensitive_keys)


def _secrets_only_under_sensitive_keys(tree: dict[str, Any]) -> dict[str, Any]:
    """A MARK secret under a plain key is not a secret; move it under a sensitive one."""
    out: dict[str, Any] = {}
    for key, value in tree.items():
        if isinstance(value, str) and value.startswith(MARK) and key in PLAIN:
            out[f"{key}_token"] = value
        else:
            out[key] = value
    return out


@pytest.mark.req("SEC-3", "SEC-6")
@pytest.mark.wp("P0-15")
@given(details=_tree(), credential=credentials, secret=secrets_, key=sensitive_keys)
def test_details_never_hold_tokens_or_bodies(
    details: dict[str, Any], credential: str, secret: str, key: str
) -> None:
    """T-P0-15-09
    Nested details with random secrets under sensitive keys and Tumnis credentials
    (`tmn_`, `tmt_`, `tmd_`) under any key, bare or inside longer strings: neither appears
    anywhere in `redact_details` output, which stays JSON-serialisable.
    """
    from tumnis.core.audit import redact_details  # noqa: PLC0415

    details = {**details, key: secret, "note": credential, "nested": {"items": [credential]}}
    dumped = json.dumps(redact_details(details))
    assert MARK not in dumped
    for prefix in ("tmn_", "tmt_", "tmd_"):
        assert prefix not in dumped
