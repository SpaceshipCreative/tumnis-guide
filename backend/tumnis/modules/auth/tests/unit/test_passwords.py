"""Password hashing: argon2id with the library defaults and automatic rehash (P0-13, SEC-1)."""

from __future__ import annotations

import pytest


@pytest.mark.req("SEC-1")
@pytest.mark.wp("P0-13")
@pytest.mark.xfail(strict=True, reason="spec:P0-13")
def test_argon2id_verify_and_rehash_flag() -> None:
    """T-P0-13-01
    A new hash is `$argon2id$`; the right password verifies with no new hash, a wrong one
    fails; a hash made with a lower `time_cost` verifies and returns a new hash with the
    current parameters; no stored hash (unknown user) never verifies.
    """
    from argon2 import PasswordHasher  # noqa: PLC0415

    from tumnis.modules.auth.passwords import (  # noqa: PLC0415
        HASHER,
        hash_password,
        verify_and_maybe_rehash,
    )

    stored = hash_password("correct horse battery staple")
    assert stored.startswith("$argon2id$")
    assert verify_and_maybe_rehash(stored, "correct horse battery staple") == (True, None)
    assert verify_and_maybe_rehash(stored, "wrong horse") == (False, None)
    assert verify_and_maybe_rehash(None, "correct horse battery staple") == (False, None)
    assert verify_and_maybe_rehash("not-a-hash", "correct horse battery staple") == (False, None)

    old = PasswordHasher(time_cost=1).hash("correct horse battery staple")
    ok, new_hash = verify_and_maybe_rehash(old, "correct horse battery staple")
    assert ok
    assert new_hash is not None
    assert new_hash != old
    assert new_hash.startswith("$argon2id$")
    assert not HASHER.check_needs_rehash(new_hash)
    assert verify_and_maybe_rehash(old, "wrong horse") == (False, None)
