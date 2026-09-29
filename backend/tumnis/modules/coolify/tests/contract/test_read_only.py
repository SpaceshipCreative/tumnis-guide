"""Tumnis cannot deploy anything through Coolify (P2-14, FR-12.2): the adapter's surface
is three reads, and its HTTP wrapper refuses every method but GET and HEAD before anything
leaves the process. The token's own permissions cannot be checked through the API, so this
is the read-only guarantee."""

from __future__ import annotations

import inspect
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.contract

SURFACE = {"get_application", "list_deployments", "health"}


def _public_methods(cls: type[Any]) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls)
        if not name.startswith("_") and callable(member)
    }


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
def test_adapter_has_no_write_methods() -> None:
    """T-P2-14-02
    The `CoolifyStatus` protocol is exactly get_application, list_deployments and health;
    the fake's public methods are exactly those three, and the real adapter adds exactly
    those three to the `Adapter` base (whose own public methods are the call wrapper and
    the breaker state, neither of which sends anything by itself).
    """
    from typing import get_protocol_members  # noqa: PLC0415

    from tumnis.core.adapters.base import Adapter  # noqa: PLC0415
    from tumnis.modules.coolify.adapters.coolify_status import CoolifyStatusApi  # noqa: PLC0415
    from tumnis.modules.coolify.adapters.fake import FakeCoolifyStatus  # noqa: PLC0415
    from tumnis.modules.coolify.adapters.port import CoolifyStatus  # noqa: PLC0415

    assert get_protocol_members(CoolifyStatus) == SURFACE
    assert _public_methods(FakeCoolifyStatus) == SURFACE
    assert _public_methods(Adapter) == {"call", "health_state"}
    assert _public_methods(CoolifyStatusApi) - _public_methods(Adapter) == SURFACE


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
async def test_http_wrapper_refuses_non_get() -> None:
    """T-P2-14-03
    `ReadOnlyHttp.request` raises `ReadOnlyViolation` for POST, PUT, PATCH and DELETE (in
    any letter case) before resolving the host or sending a byte; GET and HEAD go through
    the SSRF-guarded client with the bearer token.
    """
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.coolify.adapters.coolify_status import (  # noqa: PLC0415
        ReadOnlyHttp,
        ReadOnlyViolation,
    )

    sent: list[httpx.Request] = []
    resolved: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={})

    async def resolver(host: str, port: int) -> list[str]:
        resolved.append(host)
        return ["192.168.50.10"]

    http = ReadOnlyHttp(
        base_url="https://coolify.example.com",
        token="fake-read-token",
        policy=NetPolicy(mode="self-hosted"),
        resolver=resolver,
        transport=httpx.MockTransport(answer),
        timeout_s=5.0,
    )
    for method in ("POST", "PUT", "PATCH", "DELETE", "post", "Delete"):
        with pytest.raises(ReadOnlyViolation):
            await http.request(method, "/api/v1/applications/acme0site0example0000001/start")
    assert sent == []
    assert resolved == []

    await http.request("GET", "/api/v1/applications/acme0site0example0000001")
    await http.request("HEAD", "/api/v1/applications/acme0site0example0000001")
    assert [r.method for r in sent] == ["GET", "HEAD"]
    assert all(r.headers["authorization"] == "Bearer fake-read-token" for r in sent)
    assert resolved == ["coolify.example.com", "coolify.example.com"]
