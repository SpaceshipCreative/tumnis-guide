"""The OAuth port (P3-02): what connecting an MCP provider needs from its authorization
server, per the MCP authorization spec (OAuth 2.1 with PKCE, RFC 9728 protected-resource
metadata, RFC 8414 server metadata, RFC 7591 dynamic client registration).

`integrations.oauth` implements it twice: `adapters/oauth.py` over the MCP SDK's helpers
and `core.net`'s guarded client, `adapters/fake_oauth.py` in memory; both pass
`tests/contract/test_oauth_contract.py`. It lives outside `adapters/` because the api
imports it and `adapters/__init__` imports the api.
"""

from typing import Protocol

from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import BaseModel, Field

from tumnis.core.adapters.errors import AdapterRejected

ADAPTER = "integrations.oauth"


class OAuthServer(BaseModel):
    """What discovery found for one MCP server: the resource it protects and the
    authorization server's endpoints. Stored with the connection (sealed with its grant)
    so a refresh never discovers again."""

    resource: str  # the MCP server URL (RFC 8707 resource indicator)
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None = None
    scopes: list[str] = Field(default_factory=list)


class OAuthRefused(AdapterRejected):
    """The authorization server refused a grant (RFC 6749 section 5.2, e.g.
    `invalid_grant`): the code or refresh token is spent or revoked. Never retried; the
    user signs in again."""

    def __init__(self, op: str, error: str) -> None:
        super().__init__(ADAPTER, op, error)
        self.error = error


class OAuthPort(Protocol):
    async def discover(self, server_url: str) -> OAuthServer: ...

    async def register(
        self, server: OAuthServer, metadata: OAuthClientMetadata
    ) -> OAuthClientInformationFull: ...

    async def exchange(
        self,
        server: OAuthServer,
        client: OAuthClientInformationFull,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OAuthToken: ...

    async def refresh(
        self, server: OAuthServer, client: OAuthClientInformationFull, *, refresh_token: str
    ) -> OAuthToken: ...
