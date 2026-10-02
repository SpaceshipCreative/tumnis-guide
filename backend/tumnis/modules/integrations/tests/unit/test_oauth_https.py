"""The OAuth client never sends a grant in clear (P3-02 follow-up; SEC-5, SEC-9; MCP
authorization spec, Communication Security: authorization server endpoints are served over
HTTPS). Every request it makes, discovery included, goes over https, or over plain http
only when the address it connects to is a private (LAN) one (Scott's decision 7). The
endpoints discovery names are checked the same way before they are kept, and the sign-in
page, which the user's browser opens, must be https."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.net import NetPolicy, SsrfBlocked
from tumnis.modules.integrations.adapters.oauth import McpOAuthClient
from tumnis.modules.integrations.oauth_port import OAuthServer, require_https

PUBLIC = "203.0.113.10"  # TEST-NET-3
LAN = "192.168.1.20"
CLIENT = OAuthClientInformationFull(
    client_id="client-1", redirect_uris=[AnyUrl("https://tumnis.example.org/cb")]
)
TOKEN = {"access_token": "a-1", "token_type": "Bearer", "expires_in": 3600}


def _server(base: str, *, authorize: str | None = None) -> OAuthServer:
    return OAuthServer(
        resource=f"{base}/mcp",
        issuer=base,
        authorization_endpoint=authorize or f"{base}/authorize",
        token_endpoint=f"{base}/token",
        registration_endpoint=f"{base}/register",
    )


def _resolver(address: str) -> Callable[[str, int], Any]:
    async def resolve(host: str, port: int) -> list[str]:
        return [address]

    return resolve


class _Recorded:
    def __init__(self, metadata: dict[str, Any] | None = None) -> None:
        self.metadata = metadata
        self.sent: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        path = request.url.path
        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx.Response(404)
        if path.startswith("/.well-known/") and self.metadata is not None:
            return httpx.Response(200, content=json.dumps(self.metadata).encode())
        if path == "/token":
            return httpx.Response(200, content=json.dumps(TOKEN).encode())
        return httpx.Response(404)


def _client(handler: _Recorded, *, address: str = PUBLIC, mode: str = "hosted") -> McpOAuthClient:
    return McpOAuthClient(
        policy=NetPolicy(mode=mode),  # type: ignore[arg-type]
        resolver=_resolver(address),
        transport=httpx.MockTransport(handler),
    )


def _metadata(base: str, **overrides: str) -> dict[str, Any]:
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/authorize",
        "token_endpoint": f"{base}/token",
        "registration_endpoint": f"{base}/register",
        "response_types_supported": ["code"],
        **overrides,
    }


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_a_refresh_never_sends_the_refresh_token_over_plain_http() -> None:
    handler = _Recorded()
    with pytest.raises(SsrfBlocked):
        await _client(handler).refresh(
            _server("http://auth.example.com"), CLIENT, refresh_token="refresh-1"
        )
    assert handler.sent == []


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_an_exchange_never_sends_the_code_and_verifier_over_plain_http() -> None:
    handler = _Recorded()
    with pytest.raises(SsrfBlocked):
        await _client(handler).exchange(
            _server("http://auth.example.com"),
            CLIENT,
            code="code-1",
            code_verifier="verifier-1",
            redirect_uri="https://tumnis.example.org/cb",
        )
    assert handler.sent == []


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_plain_http_is_allowed_to_a_lan_address_only() -> None:
    """Decision 7: a self-hosted server on the homelab LAN may answer over plain http."""
    handler = _Recorded()
    token = await _client(handler, address=LAN, mode="self-hosted").refresh(
        _server("http://inbox.lan:8080"), CLIENT, refresh_token="refresh-1"
    )
    assert token.access_token == "a-1"
    assert [r.url.scheme for r in handler.sent] == ["http"]


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_discovery_of_a_plain_http_server_sends_nothing() -> None:
    handler = _Recorded(_metadata("http://mcp.example.com"))
    with pytest.raises(SsrfBlocked):
        await _client(handler).discover("http://mcp.example.com/mcp")
    assert handler.sent == []


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
@pytest.mark.parametrize(
    "field", ["issuer", "authorization_endpoint", "token_endpoint", "registration_endpoint"]
)
async def test_discovery_refuses_metadata_naming_a_plain_http_endpoint(field: str) -> None:
    base = "https://auth.example.com"
    plain = {"issuer": "http://auth.example.com"} if field == "issuer" else {}
    plain |= {} if field == "issuer" else {field: f"http://auth.example.com/{field}"}
    handler = _Recorded(_metadata(base, **plain))
    with pytest.raises(AdapterRejected):
        await _client(handler).discover("https://auth.example.com/mcp")


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
async def test_discovery_keeps_lan_http_endpoints_but_not_an_http_sign_in_page() -> None:
    """The token and registration endpoints may be LAN http (decision 7); the sign-in page
    is opened by the browser, which no SSRF guard checks, so it must be https."""
    base = "http://inbox.lan:8080"
    lan = _client(_Recorded(_metadata(base)), address=LAN, mode="self-hosted")
    with pytest.raises(AdapterRejected):
        await lan.discover(f"{base}/mcp")

    signed = _metadata(base, authorization_endpoint="https://inbox.example.com/authorize")
    found = await _client(_Recorded(signed), address=LAN, mode="self-hosted").discover(
        f"{base}/mcp"
    )
    assert found.token_endpoint == f"{base}/token"
    assert found.authorization_endpoint == "https://inbox.example.com/authorize"


@pytest.mark.req("SEC-5", "SEC-9", "FR-14.4")
@pytest.mark.wp("P3-02")
@pytest.mark.parametrize(
    "url",
    [
        "http://auth.example.com/authorize",
        "javascript:alert(1)",
        "data:text/html,hi",
        "//auth.example.com/authorize",
        "https:///authorize",
    ],
)
def test_a_sign_in_page_must_be_an_https_url(url: str) -> None:
    with pytest.raises(AdapterRejected):
        require_https(url, "authorize")
    require_https("https://auth.example.com/authorize", "authorize")
