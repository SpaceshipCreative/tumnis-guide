"""How `McpOAuthClient` reads a token endpoint's answer (P3-02, FR-14.4, RFC 6749 s5.2):
only a refused grant (400 or 401) means the user must sign in again; a rate limit or a
timeout is transient (retried, the connection stays signed in); any other 4xx is a
rejection, not a reason to drop the grant."""

from __future__ import annotations

import httpx
import pytest
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.net import NetPolicy
from tumnis.modules.integrations.adapters.oauth import McpOAuthClient
from tumnis.modules.integrations.oauth_port import OAuthRefused, OAuthServer

ISSUER = "https://auth.example.com"
SERVER = OAuthServer(
    resource="https://mcp.example.com/mcp",
    issuer=ISSUER,
    authorization_endpoint=f"{ISSUER}/authorize",
    token_endpoint=f"{ISSUER}/token",
)
CLIENT = OAuthClientInformationFull(
    client_id="client-1", redirect_uris=[AnyUrl("https://tumnis.example.org/cb")]
)


async def _public(host: str, port: int) -> list[str]:
    return ["203.0.113.10"]  # TEST-NET-3


def _client(status: int, body: bytes = b"{}") -> McpOAuthClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=body)

    return McpOAuthClient(
        policy=NetPolicy(mode="hosted"),
        resolver=_public,
        transport=httpx.MockTransport(handler),
    )


async def _refresh(client: McpOAuthClient) -> None:
    await client.refresh(SERVER, CLIENT, refresh_token="refresh-1")


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
@pytest.mark.parametrize("status", [400, 401])
async def test_a_refused_grant_raises_oauth_refused(status: int) -> None:
    with pytest.raises(OAuthRefused) as refused:
        await _refresh(_client(status, b'{"error": "invalid_grant"}'))
    assert refused.value.error == "invalid_grant"


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
@pytest.mark.parametrize("status", [408, 429, 503])
async def test_a_rate_limit_or_timeout_is_transient(status: int) -> None:
    with pytest.raises(AdapterUnavailable):
        await _refresh(_client(status))


@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
@pytest.mark.parametrize("status", [403, 404])
async def test_another_client_error_is_a_rejection_not_a_refused_grant(status: int) -> None:
    with pytest.raises(AdapterRejected):
        await _refresh(_client(status))
