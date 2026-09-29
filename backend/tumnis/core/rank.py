"""Fractional ranking keys (P0-17, FR-2.1): moving one row writes one row.

A port of David Greenspan's fractional indexing as rocicorp/fractional-indexing implements
it (CC0; https://observablehq.com/@dgreensp/implementing-fractional-indexing). A key is an
integer part (head `a`..`z`: 2 to 27 characters; `A`..`Z`: the negative mirror) and a
base-62 fraction with no trailing `0`. Keys compare as plain strings (bytewise; Postgres
columns holding them are `COLLATE "C"`). Appending stays short (`a0`, `a1`, ... `az`,
`b00`); inserting again and again at one spot grows a key by about one character per six
moves, so writers rebalance (`n_keys`) before a key reaches `MAX_KEY_LEN`.

Pure: no I/O. `frontend/src/lib/rank.ts` is the same algorithm; both pass
`backend/fixtures/rank/vectors.json`. Unlike the original, `between` raises `RankError` when
`a >= b` instead of swapping, so a client bug surfaces as 422 `invalid_rank`.
"""

from typing import Final

DIGITS: Final = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
ZERO: Final = DIGITS[0]
SMALLEST_INTEGER: Final = "A" + ZERO * 26  # has no key below it
MAX_KEY_LEN: Final = 48  # plan default: writers rebalance rather than store a key this long


class RankError(ValueError):
    """An invalid key, or a >= b; answered 422 `invalid_rank`."""

    code = "invalid_rank"


def _int_length(head: str) -> int:
    if "a" <= head <= "z":
        return ord(head) - ord("a") + 2
    if "A" <= head <= "Z":
        return ord("Z") - ord(head) + 2
    raise RankError(f"invalid rank key head: {head!r}")


def _int_part(key: str) -> str:
    if not key:
        raise RankError("empty rank key")
    length = _int_length(key[0])
    if length > len(key):
        raise RankError(f"invalid rank key: {key!r}")
    return key[:length]


def validate(key: str) -> None:
    """RankError unless `key` is a well-formed key: a valid head, a full integer part,
    base-62 digits and no trailing zero in the fraction."""
    if key == SMALLEST_INTEGER:
        raise RankError(f"invalid rank key: {key!r}")
    integer = _int_part(key)
    if any(ch not in DIGITS for ch in key[1:]):
        raise RankError(f"invalid rank key: {key!r}")
    if key[len(integer) :].endswith(ZERO):
        raise RankError(f"invalid rank key (trailing zero): {key!r}")


def _midpoint(a: str, b: str | None) -> str:
    """A fraction strictly between fractions a and b (b None: no upper bound)."""
    if b is not None:
        # Drop the longest common prefix, padding `a` with zeros as we go (b cannot end
        # before a inside the common prefix).
        n = 0
        while n < len(b) and (a[n] if n < len(a) else ZERO) == b[n]:
            n += 1
        if n > 0:
            return b[:n] + _midpoint(a[n:], b[n:])
    digit_a = DIGITS.index(a[0]) if a else 0
    digit_b = DIGITS.index(b[0]) if b is not None else len(DIGITS)
    if digit_b - digit_a > 1:
        return DIGITS[(digit_a + digit_b + 1) // 2]  # Math.round of the mean, as in JS
    if b is not None and len(b) > 1:
        return b[:1]
    return DIGITS[digit_a] + _midpoint(a[1:], None)


def _incr(integer: str) -> str | None:
    """The next integer part, or None past the largest (`z` + 26 `z`s)."""
    head, digits = integer[0], list(integer[1:])
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) + 1
        if d < len(DIGITS):
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = ZERO
    if head == "Z":
        return "a" + ZERO
    if head == "z":
        return None
    new_head = chr(ord(head) + 1)
    if new_head > "a":
        digits.append(ZERO)
    else:
        digits.pop()
    return new_head + "".join(digits)


def _decr(integer: str) -> str | None:
    """The previous integer part, or None below the smallest."""
    head, digits = integer[0], list(integer[1:])
    top = DIGITS[-1]
    for i in range(len(digits) - 1, -1, -1):
        d = DIGITS.index(digits[i]) - 1
        if d >= 0:
            digits[i] = DIGITS[d]
            return head + "".join(digits)
        digits[i] = top
    if head == "a":
        return "Z" + top
    if head == "A":  # pragma: no cover  # only below SMALLEST_INTEGER, never asked for
        return None
    new_head = chr(ord(head) - 1)
    if new_head < "Z":
        digits.append(top)
    else:
        digits.pop()
    return new_head + "".join(digits)


def _before(b: str) -> str:
    """A key below b."""
    int_b = _int_part(b)
    if int_b == SMALLEST_INTEGER:
        return int_b + _midpoint("", b[len(int_b) :])
    if int_b < b:
        return int_b
    below = _decr(int_b)
    if below is None or below == SMALLEST_INTEGER:  # the smallest integer is no key
        return SMALLEST_INTEGER + _midpoint("", None)
    return below


def _after(a: str) -> str:
    """A key above a."""
    int_a = _int_part(a)
    above = _incr(int_a)
    return int_a + _midpoint(a[len(int_a) :], None) if above is None else above


def between(a: str | None, b: str | None) -> str:
    """A key strictly between a and b (None: an open end). a < b is required."""
    for key in (a, b):
        if key is not None:
            validate(key)
    if a is None:
        return "a" + ZERO if b is None else _before(b)
    if b is None:
        return _after(a)
    if a >= b:
        raise RankError(f"{a!r} is not before {b!r}")
    int_a, int_b = _int_part(a), _int_part(b)
    if int_a == int_b:
        return int_a + _midpoint(a[len(int_a) :], b[len(int_b) :])
    above = _incr(int_a)
    if above is not None and above < b:
        return above
    return int_a + _midpoint(a[len(int_a) :], None)


def n_keys(n: int) -> list[str]:
    """n increasing keys from the start (`a0`, `a1`, ...): the short keys a rebalance or a
    seed writes."""
    keys: list[str] = []
    for _ in range(n):
        keys.append(between(keys[-1] if keys else None, None))
    return keys
