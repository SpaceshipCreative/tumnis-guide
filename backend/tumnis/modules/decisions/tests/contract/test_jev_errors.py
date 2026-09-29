"""How JevDecisions maps the SDK's failures onto the adapter errors (P1-01, P0-09)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx2
import pytest
from pydantic import SecretStr

from tumnis.core.adapters.errors import AdapterRejected, AdapterTimeout, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.modules.decisions.adapters import build_jev
from tumnis.modules.decisions.adapters.jev import JevDecisions, UnpinnedModel
from tumnis.modules.decisions.catalog import DecisionPoint, build_request
from tumnis.modules.decisions.tests._cases import PINNED_MODEL, stored_inputs

pytestmark = pytest.mark.contract
CLOCK = FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC))
KEY = SecretStr("ts_test_0000000000000000000000000000")


def _jev(handler: object) -> JevDecisions:
    transport = httpx2.MockTransport(handler)  # type: ignore[arg-type]
    return JevDecisions(KEY, PINNED_MODEL, clock=CLOCK, transport=transport)


def _raise(exc: Exception) -> object:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise exc

    return handler


def _status(status: int) -> object:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(status, json={"error": {"message": "no"}})

    return handler


@pytest.mark.req("FR-11.1")
@pytest.mark.wp("P1-01")
@pytest.mark.parametrize(
    ("handler", "error", "retryable"),
    [
        (_status(500), AdapterUnavailable, True),
        (_status(503), AdapterUnavailable, True),
        (_status(408), AdapterUnavailable, True),
        (_status(400), AdapterRejected, False),
        (_status(401), AdapterRejected, False),
        (_status(422), AdapterRejected, False),
        (_raise(httpx2.ConnectError("refused")), AdapterUnavailable, True),
        (_raise(httpx2.ReadTimeout("slow")), AdapterTimeout, True),
    ],
)
async def test_sdk_failures_become_adapter_errors(
    handler: object, error: type[Exception], retryable: bool
) -> None:
    """5xx, 408 and connection failures are retryable AdapterUnavailable, a timeout is
    AdapterTimeout, any other 4xx is AdapterRejected (the provider said no)."""
    jev = _jev(handler)
    req = build_request(DecisionPoint.ACTIONABILITY, stored_inputs("actionability"))
    with pytest.raises(error) as failed:
        await jev.ask(req, model=PINNED_MODEL, timeout_ms=2_000)
    assert failed.value.retryable is retryable  # type: ignore[attr-defined]
    await jev.aclose()


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
def test_the_registry_builds_jev_lazily_and_refuses_an_alias() -> None:
    """The registry's real factory imports the adapter on first use; a JevDecisions pinned
    to an alias cannot be built."""
    built = build_jev(api_key=KEY, pinned_model=PINNED_MODEL, clock=CLOCK)
    assert isinstance(built, JevDecisions)
    assert built.provider == "jev"
    with pytest.raises(UnpinnedModel):
        JevDecisions(KEY, "jev-latest", clock=CLOCK)
