"""The OAuth port's contract (P3-02, FR-14.4): the in-memory fake and the real HTTP client,
run against the fake's authorization server over HTTP (`asgi_app()`), behave the same
(AGENTS.md: fakes obey the real contract)."""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import pytest
from mcp.client.auth import PKCEParameters
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata
from pydantic import AnyUrl

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.net import NetPolicy
from tumnis.modules.integrations.adapters.fake_oauth import ISSUER, RESOURCE, FakeOAuthServer
from tumnis.modules.integrations.adapters.oauth import McpOAuthClient
from tumnis.modules.integrations.oauth_port import ADAPTER, OAuthPort, OAuthRefused, OAuthServer

REDIRECT = "https://tumnis.example.org/v1/connections/oauth/callback"
PUBLIC_ADDRESS = "203.0.113.10"  # TEST-NET-3: what the resolver answers for the fake hosts


def _metadata() -> OAuthClientMetadata:
    return OAuthClientMetadata(
        redirect_uris=[AnyUrl(REDIRECT)],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",  # a public client
    )


def _approve(
    server: FakeOAuthServer,
    found: OAuthServer,
    client: OAuthClientInformationFull,
    pkce: PKCEParameters,
    code: str,
) -> str:
    query = {
        "response_type": "code",
        "client_id": client.client_id or "",
        "redirect_uri": REDIRECT,
        "state": "state-1",
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
        "resource": found.resource,
    }
    return server.approve(f"{found.authorization_endpoint}?{urlencode(query)}", code=code)


class OAuthContract(AdapterContract[OAuthPort]):
    port, adapter_name = OAuthPort, ADAPTER

    @pytest.fixture
    def server(self) -> FakeOAuthServer:
        return FakeOAuthServer()

    async def test_discovery_names_the_authorization_server(self, subject: OAuthPort) -> None:
        found = await subject.discover(RESOURCE)
        assert found.resource == RESOURCE
        assert found.issuer.rstrip("/") == ISSUER
        assert found.authorization_endpoint == f"{ISSUER}/authorize"
        assert found.token_endpoint == f"{ISSUER}/token"
        assert found.registration_endpoint == f"{ISSUER}/register"
        assert found.scopes

    async def test_registration_issues_a_public_client(
        self, subject: OAuthPort, server: FakeOAuthServer
    ) -> None:
        client = await subject.register(await subject.discover(RESOURCE), _metadata())
        assert client.client_id in server.clients
        assert client.client_secret is None
        assert [str(u) for u in client.redirect_uris or []] == [REDIRECT]

    async def test_code_exchange_needs_the_verifier_and_is_one_use(
        self, subject: OAuthPort, server: FakeOAuthServer
    ) -> None:
        found = await subject.discover(RESOURCE)
        client = await subject.register(found, _metadata())
        pkce = PKCEParameters.generate()
        code = _approve(server, found, client, pkce, "code-1")
        with pytest.raises(OAuthRefused) as wrong:
            await subject.exchange(
                found, client, code=code, code_verifier="w" * 64, redirect_uri=REDIRECT
            )
        assert wrong.value.error == "invalid_grant"

        code = _approve(server, found, client, pkce, "code-2")
        tokens = await subject.exchange(
            found, client, code=code, code_verifier=pkce.code_verifier, redirect_uri=REDIRECT
        )
        assert tokens.access_token
        assert tokens.refresh_token
        with pytest.raises(OAuthRefused):
            await subject.exchange(
                found, client, code=code, code_verifier=pkce.code_verifier, redirect_uri=REDIRECT
            )

    async def test_refresh_rotates_the_refresh_token(
        self, subject: OAuthPort, server: FakeOAuthServer
    ) -> None:
        found = await subject.discover(RESOURCE)
        client = await subject.register(found, _metadata())
        first = server.issue(client.client_id or "", expires_in=60)
        assert first.refresh_token
        fresh = await subject.refresh(found, client, refresh_token=first.refresh_token)
        assert fresh.access_token != first.access_token
        assert fresh.refresh_token
        assert fresh.refresh_token != first.refresh_token
        assert not server.refresh_token_live(first.refresh_token)
        with pytest.raises(OAuthRefused) as spent:
            await subject.refresh(found, client, refresh_token=first.refresh_token)
        assert spent.value.error == "invalid_grant"


@pytest.mark.contract
@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
class TestOAuthFake(OAuthContract):
    impl = "fake"

    @pytest.fixture
    def subject(self, server: FakeOAuthServer) -> OAuthPort:
        return server


async def _public(host: str, port: int) -> list[str]:
    return [PUBLIC_ADDRESS]


@pytest.mark.contract
@pytest.mark.req("FR-14.4")
@pytest.mark.wp("P3-02")
class TestOAuthReal(OAuthContract):
    """The real client, through `core.net`'s guard, against the fake server over HTTP."""

    impl = "real"

    @pytest.fixture
    def subject(self, server: FakeOAuthServer) -> OAuthPort:
        return McpOAuthClient(
            policy=NetPolicy(mode="hosted"),
            resolver=_public,
            transport=httpx.ASGITransport(app=server.asgi_app()),
        )
