"""Password hashing: argon2id through argon2-cffi (P0-13, SEC-1). Spec skeleton."""

from argon2 import PasswordHasher

HASHER = PasswordHasher()  # library defaults: argon2id, RFC 9106 low-memory profile


def hash_password(password: str) -> str:
    raise NotImplementedError("P0-13")


def verify_and_maybe_rehash(stored: str | None, password: str) -> tuple[bool, str | None]:
    raise NotImplementedError("P0-13")
