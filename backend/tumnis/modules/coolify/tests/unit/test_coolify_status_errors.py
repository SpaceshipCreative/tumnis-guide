"""How the real Coolify client answers failures (P2-14): 5xx and 429 are unavailable,
other refusals and unexpected shapes rejected, and no error text carries the token."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tumnis.core.adapters.errors import AdapterError, AdapterRejected, AdapterUnavailable
from tumnis.core.net import NetPolicy, SsrfBlocked
from tumnis.modules.coolify.adapters.coolify_status import CoolifyStatusApi
from tumnis.modules.coolify.tests.replay import BASE_URL, TOKEN

APP = "acme0site0example0000001"


async def _lan(host: str, port: int) -> list[str]:
    return ["192.168.50.10"]


def _api(answer: httpx.Response | Exception, policy: str = "self-hosted") -> CoolifyStatusApi:
    def handle(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return answer

    return CoolifyStatusApi(
        base_url=BASE_URL,
        token=TOKEN,
        policy=NetPolicy(mode=policy),  # type: ignore[arg-type]
        resolver=_lan,
        transport=httpx.MockTransport(handle),
    )


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (httpx.Response(503, json={"message": "down"}), AdapterUnavailable),
        (httpx.Response(429, headers={"Retry-After": "30"}), AdapterUnavailable),
        (httpx.ConnectError("refused"), AdapterUnavailable),
        (httpx.Response(401, json={"message": "Unauthenticated."}), AdapterRejected),
        (httpx.Response(404, json={"message": "Application not found"}), AdapterRejected),
        (httpx.Response(302, headers={"Location": "https://example.org/login"}), AdapterRejected),
        (httpx.Response(200, text="<html>login</html>"), AdapterRejected),
        (httpx.Response(200, json={"uuid": APP}), AdapterRejected),  # no name: a new shape
    ],
)
async def test_failures_map_to_adapter_errors(
    answer: httpx.Response | Exception, error: type[AdapterError]
) -> None:
    with pytest.raises(error) as caught:
        await _api(answer).get_application(APP)
    assert TOKEN not in str(caught.value)
    if isinstance(answer, httpx.Response) and answer.status_code == 429:
        assert caught.value.retry_after_s == 30.0


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_deployments_of_an_unexpected_shape_are_rejected() -> None:
    body: dict[str, Any] = {"count": 1, "deployments": {"not": "a list"}}
    with pytest.raises(AdapterRejected):
        await _api(httpx.Response(200, json=body)).list_deployments(APP)
    listed = await _api(httpx.Response(200, json=[])).list_deployments(APP)  # a bare list
    assert listed == []


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_hosted_mode_refuses_a_lan_coolify() -> None:
    """The SSRF guard still decides: in hosted mode a private address is refused."""
    with pytest.raises(SsrfBlocked):
        await _api(httpx.Response(200, json={}), policy="hosted").get_application(APP)


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_a_uuid_stays_one_path_segment() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        return httpx.Response(404, json={"message": "Application not found"})

    api = CoolifyStatusApi(
        base_url=BASE_URL,
        token=TOKEN,
        policy=NetPolicy(mode="self-hosted"),
        resolver=_lan,
        transport=httpx.MockTransport(handle),
    )
    with pytest.raises(AdapterRejected):
        await api.get_application("../x/start")
    assert seen == ["/api/v1/applications/..%2Fx%2Fstart"]
