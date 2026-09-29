"""Password hashing: argon2id through argon2-cffi with the library defaults (P0-13, SEC-1).

`verify_and_maybe_rehash` also returns a new hash when the stored one was made with other
parameters, so raising the defaults upgrades every account at its next sign-in. Unknown
users verify against `DUMMY_HASH`, so a wrong email costs the same time as a wrong
password.
"""

from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

HASHER = PasswordHasher()  # library defaults: argon2id, RFC 9106 low-memory profile
DUMMY_HASH: Final = HASHER.hash("dummy-password-for-timing")


def hash_password(password: str) -> str:
    return HASHER.hash(password)


def verify_and_maybe_rehash(stored: str | None, password: str) -> tuple[bool, str | None]:
    """Returns (ok, new_hash_or_None). Unknown users verify against DUMMY_HASH so timing
    matches; a malformed stored hash never verifies."""
    try:
        HASHER.verify(stored or DUMMY_HASH, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None
    if stored is None:
        return False, None
    return True, HASHER.hash(password) if HASHER.check_needs_rehash(stored) else None
