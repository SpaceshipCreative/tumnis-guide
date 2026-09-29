"""API key secrets: format, entropy and constant-time verification (P0-14, SEC-2, ADR-0010)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tumnis.core.crypto import MasterKeys


def _peppers() -> MasterKeys:
    from tumnis.core.crypto import MasterKeys  # noqa: PLC0415

    return MasterKeys(active=1, keys={1: secrets.token_bytes(32)})


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
def test_generated_key_format_and_entropy() -> None:
    """T-P0-14-01
    A generated key is `tmn_` + a 12-character base32 prefix + a 43-character url-safe
    secret; its HMAC is HMAC-SHA256(pepper, secret) under the active pepper version; 1,000
    keys have distinct prefixes and distinct secrets. Task and device tokens use `tmt_` and
    `tmd_` and parse back to their parts.
    """
    from tumnis.modules.auth.keys import KEY_RE, generate, parse  # noqa: PLC0415

    peppers = _peppers()
    made = [generate("tmn", peppers) for _ in range(1_000)]
    shape = re.compile(r"^tmn_[a-z2-7]{12}_[A-Za-z0-9_-]{43}$")
    for new in made:
        assert shape.fullmatch(new.display), new.display
        assert KEY_RE.fullmatch(new.display)
        kind, prefix, secret = new.display.split("_", 2)
        assert kind == "tmn"
        assert new.prefix == prefix
        assert new.pepper_version == 1
        pepper = peppers.keys[1]
        assert new.secret_hmac == hmac.new(pepper, secret.encode(), hashlib.sha256).digest()
        parsed = parse(new.display)
        assert parsed is not None
        assert (parsed.kind, parsed.prefix, parsed.secret) == ("tmn", prefix, secret)
    assert len({new.prefix for new in made}) == 1_000
    assert len({new.display.split("_", 2)[2] for new in made}) == 1_000

    for kind, token in (("tmt", generate("tmt", peppers)), ("tmd", generate("tmd", peppers))):
        assert token.display.startswith(f"{kind}_")
        parsed = parse(token.display)
        assert parsed is not None
        assert parsed.kind == kind
    for bad in ("tmn_short_secret", "xyz_" + "a" * 12 + "_" + "b" * 43, "", "Bearer x"):
        assert parse(bad) is None


@pytest.mark.req("SEC-2")
@pytest.mark.wp("P0-14")
def test_verification_uses_constant_time_compare(monkeypatch: pytest.MonkeyPatch) -> None:
    """T-P0-14-03
    `verify` checks every row with the prefix through `hmac.compare_digest` (a spy counts
    the calls), including the rows after a match, and returns the matching row; a wrong
    secret matches nothing after the same number of comparisons.
    """
    from tumnis.modules.auth import keys  # noqa: PLC0415

    peppers = _peppers()
    new = keys.generate("tmn", peppers)
    secret = new.display.split("_", 2)[2]

    def row(secret_hmac: bytes) -> keys.KeyRow:
        return keys.KeyRow(
            key_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            secret_hmac=secret_hmac,
            pepper_version=1,
            scopes=frozenset(),
            project_ids=None,
            expires_at=None,
            revoked_at=None,
        )

    match = row(new.secret_hmac)
    rows = [match, row(secrets.token_bytes(32)), row(secrets.token_bytes(32))]

    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(hmac, "compare_digest", spy)

    assert keys.verify(secret, rows, peppers) == match
    assert len(calls) == 3  # no early exit after the first row matched
    calls.clear()
    assert keys.verify(secret + "x", rows, peppers) is None
    assert len(calls) == 3
