"""UUIDv7 for IDs made in Python (P0-06, ADR-0005).

Rows get their IDs from Postgres 18's `uuidv7()` default; code that needs an ID before the
insert (an outbox event, a workflow ID) calls `uuid7()`. Python 3.14 ships `uuid.uuid7`;
on 3.13 this module implements RFC 9562 section 5.7 with the 12-bit `rand_a` field as a
counter (method 1 of section 6.2), so IDs made in one process sort in creation order even
within a millisecond.
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = -1
_counter = 0
_COUNTER_MAX = 0xFFF


def _uuid7_fallback() -> uuid.UUID:
    global _last_ms, _counter  # noqa: PLW0603  # process-wide monotonic state
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms > _last_ms:
            _last_ms, _counter = ms, int.from_bytes(os.urandom(2)) & 0x3FF
        elif _counter < _COUNTER_MAX:
            _counter += 1
        else:  # counter spent within this millisecond: borrow the next one
            _last_ms, _counter = _last_ms + 1, 0
        ms, counter = _last_ms, _counter
    rand_b = int.from_bytes(os.urandom(8)) & ((1 << 62) - 1)
    value = (ms & ((1 << 48) - 1)) << 80 | 0x7 << 76 | counter << 64 | 0b10 << 62 | rand_b
    return uuid.UUID(int=value)


def uuid7() -> uuid.UUID:
    """A version 7 UUID: 48-bit Unix milliseconds, then counter and random bits."""
    native = getattr(uuid, "uuid7", None)
    if native is not None:  # pragma: no cover  # Python 3.14
        result: uuid.UUID = native()
        return result
    return _uuid7_fallback()
