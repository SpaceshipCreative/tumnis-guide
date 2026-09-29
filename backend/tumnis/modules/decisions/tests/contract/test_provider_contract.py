"""The decisions provider contract (P1-01) over the fake and over `JevDecisions` replaying
recorded Jev answers, plus the Jev adapter's pinned model and its error mapping."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import SecretStr

from tumnis.modules.decisions.adapters.port import DecisionsProvider
from tumnis.modules.decisions.tests._cases import PINNED_MODEL, load_jev_recordings
from tumnis.modules.decisions.tests.contract.base import (
    DecisionsProviderContract,
    replay_transport,
)

if TYPE_CHECKING:
    import httpx2

    from tests.fixtures import Fakes

pytestmark = pytest.mark.contract

TEST_KEY = SecretStr("ts_test_0000000000000000000000000000")


def _jev(transport: httpx2.MockTransport) -> Any:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.jev import JevDecisions  # noqa: PLC0415

    return JevDecisions(
        TEST_KEY,
        PINNED_MODEL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        transport=transport,
    )


def _recording(name: str) -> dict[str, Any]:
    return dict(load_jev_recordings())[name]


@pytest.mark.req("FR-11.1")
@pytest.mark.wp("P1-01")
class TestFakeProvider(DecisionsProviderContract):
    """T-P1-01-08"""

    impl = "fake"

    @pytest.fixture
    def subject(self, fakes: Fakes) -> DecisionsProvider:
        provider: DecisionsProvider = fakes["decisions.jev"]
        return provider


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
class TestJevOnRecordings(DecisionsProviderContract):
    """T-P1-01-09"""

    impl = "recorded"

    @pytest.fixture
    def subject(self) -> DecisionsProvider:
        provider: DecisionsProvider = _jev(
            replay_transport([rec for _, rec in load_jev_recordings()])
        )
        return provider


@pytest.mark.req("FR-11.2")
@pytest.mark.wp("P1-01")
async def test_pinned_model_is_sent_and_answering_model_returned() -> None:
    """T-P1-01-12
    The request body carries the pinned id (`jev-1.13.0`), never an alias; the response's
    versioned id comes back as `ProviderResponse.model`; the adapter's structured log line
    records both ids with the point, token count and latency, and neither the state nor
    the question text. Asking with an alias is refused before anything is sent.
    """
    from structlog.testing import capture_logs  # noqa: PLC0415

    from tumnis.modules.decisions.adapters.jev import UnpinnedModel  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import (  # noqa: PLC0415
        CATALOGUE,
        DecisionPoint,
        build_request,
    )

    rec = _recording("quick_add_label__human.json")
    seen: list[dict[str, Any]] = []
    jev = _jev(replay_transport([rec], seen))
    req = build_request(DecisionPoint.QUICK_ADD_LABEL, rec["inputs"])
    timeout_ms = CATALOGUE[req.point].timeout_ms

    with capture_logs() as logs:
        resp = await jev.ask(req, model=PINNED_MODEL, timeout_ms=timeout_ms)

    assert [body["model"] for body in seen] == ["jev-1.13.0"]
    assert resp.provider == "jev"
    assert resp.model == rec["response"]["body"]["model"]
    assert resp.input_tokens == rec["response"]["body"]["usage"]["input_tokens"]
    lines = [line for line in logs if line["event"] == "decisions.provider_call"]
    assert len(lines) == 1
    line = lines[0]
    assert line["point"] == "quick_add_label"
    assert line["provider"] == "jev"
    assert line["model_requested"] == "jev-1.13.0"
    assert line["model_answered"] == resp.model
    assert line["input_tokens"] == resp.input_tokens
    assert "latency_ms" in line
    logged = repr(line)
    assert rec["inputs"]["title"] not in logged
    assert "Who should do this task" not in logged
    assert "state" not in line
    assert "questions" not in line

    for alias in ("jev-latest", "jev", "jev-1.13"):
        with pytest.raises(UnpinnedModel):
            await jev.ask(req, model=alias, timeout_ms=timeout_ms)
    assert len(seen) == 1


@pytest.mark.req("FR-11.9")
@pytest.mark.wp("P1-01")
async def test_rate_limit_error_is_retryable_and_opens_breaker() -> None:
    """T-P1-01-16
    A recorded 429 maps to the adapter base's retryable AdapterUnavailable carrying the
    server's Retry-After; each ask sends once (the workflow retries, not the adapter), and
    five consecutive failures (the P0-09 breaker threshold) open the breaker: the next ask
    raises CircuitOpen without a request and health reports "degraded".
    """
    from tumnis.core.adapters.base import AdapterUnavailable, CircuitOpen  # noqa: PLC0415
    from tumnis.core.adapters.breaker import BreakerConfig  # noqa: PLC0415
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415

    rec = _recording("quick_add_label__rate_limited.json")
    assert rec["response"]["status"] == 429
    seen: list[dict[str, Any]] = []
    jev = _jev(replay_transport([rec], seen))
    req = build_request(DecisionPoint(rec["point"]), rec["inputs"])

    threshold = BreakerConfig().failure_threshold
    for attempt in range(threshold):
        with pytest.raises(AdapterUnavailable) as failed:
            await jev.ask(req, model=PINNED_MODEL, timeout_ms=800)
        assert failed.value.retryable
        assert failed.value.retry_after_s == pytest.approx(1.5)
        assert len(seen) == attempt + 1
    assert await jev.health() == "degraded"
    with pytest.raises(CircuitOpen):
        await jev.ask(req, model=PINNED_MODEL, timeout_ms=800)
    assert len(seen) == threshold
