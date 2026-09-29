"""Pure parts of the P0-10 conventions: token buckets, the cursor codec, request hashing and
the principal's identity."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from tumnis.core.clock import FixedClock

AT = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-10")
def test_token_bucket_allows_the_burst_then_refills() -> None:
    from tumnis.core.ratelimit import Bucket, RateLimiter  # noqa: PLC0415

    clock = FixedClock(AT)
    limiter = RateLimiter(clock, {"b": Bucket(rate_per_s=2, burst=3)})
    assert [limiter.check("b", "x") for _ in range(3)] == [None, None, None]
    assert limiter.check("b", "x") == pytest.approx(0.5)
    assert limiter.check("b", "y") is None  # another subject has its own bucket
    clock.advance(seconds=0.5)
    assert limiter.check("b", "x") is None
    assert limiter.check("b", "x") is not None


@pytest.mark.req("PERF-1")
@pytest.mark.wp("P0-10")
def test_cursor_round_trips_and_refuses_the_wrong_shape() -> None:
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.pagination import Cursor  # noqa: PLC0415

    row_id = uuid.uuid4()
    cursor = Cursor((date(2026, 3, 9), AT), row_id)
    assert Cursor.decode(cursor.encode(), [date, datetime]) == cursor
    for kinds in ([date], [int, datetime]):
        with pytest.raises(ProblemError) as refused:
            Cursor.decode(cursor.encode(), kinds)
        assert refused.value.code == "invalid_cursor"


@pytest.mark.req("REL-2")
@pytest.mark.wp("P0-10")
def test_request_hash_ignores_json_key_order_only() -> None:
    from tumnis.core.idempotency import request_hash  # noqa: PLC0415

    one = request_hash("POST", "/v1/x", b'{"a": 1, "b": 2}')
    assert one == request_hash("POST", "/v1/x", b'{"b":2,"a":1}')
    assert one != request_hash("POST", "/v1/y", b'{"a": 1, "b": 2}')
    assert one != request_hash("PUT", "/v1/x", b'{"a": 1, "b": 2}')
    assert request_hash("POST", "/v1/x", b"raw") != request_hash("POST", "/v1/x", b"raw ")


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-10")
def test_principal_acts_as_its_actor_and_anonymous_as_nobody() -> None:
    from tumnis.core.errors import ProblemError  # noqa: PLC0415
    from tumnis.core.principal import ANONYMOUS, Principal  # noqa: PLC0415

    ws, key = uuid.uuid4(), uuid.uuid4()
    principal = Principal(kind="api_key", workspace_id=ws, subject_id=key)
    assert principal.key == f"api_key:{key}"
    assert principal.workspace_context().workspace_id == ws
    assert ANONYMOUS.anonymous
    with pytest.raises(ProblemError) as refused:
        ANONYMOUS.workspace_context()
    assert refused.value.status == 401
