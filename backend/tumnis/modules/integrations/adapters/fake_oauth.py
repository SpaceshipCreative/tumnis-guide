"""`FakeOAuthServer`: the OAuth port (`integrations.oauth`) in memory, and the same
authorization server over HTTP (`asgi_app()`) for the real client's contract (P3-02).

It behaves as an MCP server's authorization server does (MCP authorization spec:
OAuth 2.1 with PKCE S256, RFC 9728 protected-resource metadata, RFC 8414 server metadata,
RFC 7591 dynamic client registration):

- `discover` / the well-known documents name its endpoints;
- `register` issues a public client id (`fake-client-<n>`);
- `approve(authorize_url, code=)` plays the user signing in at the authorize URL: it
  remembers the client, redirect URI and PKCE challenge against the one-use code;
- `exchange` checks all three (the verifier hashed with S256) and spends the code;
- `refresh` rotates the refresh token: the old one is dead afterwards (`refreshes`
  counts them, `refresh_token_live` tells);
- a refused grant raises `OAuthRefused` (`invalid_grant`, RFC 6749 section 5.2).

`calls` records every port call by name. Token strings are `fake-access-<n>` and
`fake-refresh-<n>`.
"""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qs, urlsplit

from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tumnis.core.adapters.registry import Health
from tumnis.modules.integrations.oauth_port import OAuthRefused, OAuthServer

ISSUER: Final = "https://auth.fake.example"
RESOURCE: Final = "https://mcp.fake.example/mcp"
SCOPES: Final = ("mail.read",)
TOKEN_TTL_S: Final = 3600


def s256(verifier: str) -> str:
    """The PKCE S256 challenge of `verifier` (RFC 7636 section 4.2)."""
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass(frozen=True)
class _Approved:
    client_id: str
    redirect_uri: str
    challenge: str


class FakeOAuthServer:
    def __init__(self, *, issuer: str = ISSUER, scopes: tuple[str, ...] = SCOPES) -> None:
        self.issuer = issuer
        self.scopes = scopes
        self.authorization_endpoint = f"{issuer}/authorize"
        self.token_endpoint = f"{issuer}/token"
        self.registration_endpoint = f"{issuer}/register"
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.calls: list[tuple[Any, ...]] = []
        self.refreshes = 0
        self._codes: dict[str, _Approved] = {}
        self._live_refresh: dict[str, str] = {}  # refresh token -> client id
        self._serial = 0

    # --- the port -------------------------------------------------------------------------

    async def discover(self, server_url: str) -> OAuthServer:
        self.calls.append(("discover", server_url))
        return OAuthServer(
            resource=server_url,
            issuer=self.issuer,
            authorization_endpoint=self.authorization_endpoint,
            token_endpoint=self.token_endpoint,
            registration_endpoint=self.registration_endpoint,
            scopes=list(self.scopes),
        )

    async def register(
        self, server: OAuthServer, metadata: OAuthClientMetadata
    ) -> OAuthClientInformationFull:
        self.calls.append(("register", server.issuer))
        return self._register(metadata)

    async def exchange(
        self,
        server: OAuthServer,
        client: OAuthClientInformationFull,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OAuthToken:
        self.calls.append(("exchange", client.client_id))
        return self._exchange(client.client_id or "", code, code_verifier, redirect_uri)

    async def refresh(
        self, server: OAuthServer, client: OAuthClientInformationFull, *, refresh_token: str
    ) -> OAuthToken:
        self.calls.append(("refresh", client.client_id))
        return self._refresh(client.client_id or "", refresh_token)

    async def health(self) -> Health:
        return "ok"

    # --- the user and the test's side ----------------------------------------------------

    def issue(self, client_id: str, *, expires_in: int) -> OAuthToken:
        """A fresh token set for `client_id` whose refresh token is live."""
        self._serial += 1
        refresh = f"fake-refresh-{self._serial}"
        self._live_refresh[refresh] = client_id
        return OAuthToken(
            access_token=f"fake-access-{self._serial}",
            expires_in=expires_in,
            refresh_token=refresh,
            scope=" ".join(self.scopes) or None,
        )

    def approve(self, authorize_url: str, *, code: str) -> str:
        """The user signs in at `authorize_url` and consents: returns the one-use code the
        server sends to the redirect URI. The request must name a registered client, one
        of its redirect URIs and an S256 challenge."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        client = self.clients.get(query.get("client_id", ""))
        redirect_uri = query.get("redirect_uri", "")
        if client is None or redirect_uri not in {str(u) for u in client.redirect_uris or []}:
            raise ValueError("the authorize URL names no registered client and redirect URI")
        if query.get("response_type") != "code" or query.get("code_challenge_method") != "S256":
            raise ValueError("the authorize URL must ask for a code with PKCE S256")
        self._codes[code] = _Approved(
            client.client_id or "", redirect_uri, query.get("code_challenge", "")
        )
        return code

    def refresh_token_live(self, token: str) -> bool:
        return token in self._live_refresh

    def revoke(self, refresh_token: str) -> None:
        """The user revokes the grant at the provider."""
        self._live_refresh.pop(refresh_token, None)

    # --- shared logic ---------------------------------------------------------------------

    def _register(self, metadata: OAuthClientMetadata) -> OAuthClientInformationFull:
        self._serial += 1
        client_id = f"fake-client-{self._serial}"
        info = OAuthClientInformationFull.model_validate(
            {**metadata.model_dump(mode="json", exclude_none=True), "client_id": client_id}
        )
        self.clients[client_id] = info
        return info

    def _exchange(
        self, client_id: str, code: str, code_verifier: str, redirect_uri: str
    ) -> OAuthToken:
        approved = self._codes.pop(code, None)
        if (
            approved is None
            or not hmac.compare_digest(approved.client_id.encode(), client_id.encode())
            or approved.redirect_uri != redirect_uri
            or not hmac.compare_digest(approved.challenge.encode(), s256(code_verifier).encode())
        ):
            raise OAuthRefused("exchange", "invalid_grant")
        return self.issue(client_id, expires_in=TOKEN_TTL_S)

    def _refresh(self, client_id: str, refresh_token: str) -> OAuthToken:
        owner = self._live_refresh.get(refresh_token)
        if owner is None or not hmac.compare_digest(owner.encode(), client_id.encode()):
            raise OAuthRefused("refresh", "invalid_grant")
        del self._live_refresh[refresh_token]
        self.refreshes += 1
        return self.issue(client_id, expires_in=TOKEN_TTL_S)

    # --- the same server over HTTP ---------------------------------------------------------

    def asgi_app(self, resource: str = RESOURCE) -> Starlette:
        """The well-known documents, registration and token endpoints over HTTP, so the
        real client (`adapters/oauth.py`) runs its contract against this server."""
        resource_path = urlsplit(resource).path

        async def protected_resource(_: Request) -> Response:
            return JSONResponse(
                {
                    "resource": resource,
                    "authorization_servers": [self.issuer],
                    "scopes_supported": list(self.scopes),
                }
            )

        async def server_metadata(_: Request) -> Response:
            return JSONResponse(
                {
                    "issuer": self.issuer,
                    "authorization_endpoint": self.authorization_endpoint,
                    "token_endpoint": self.token_endpoint,
                    "registration_endpoint": self.registration_endpoint,
                    "scopes_supported": list(self.scopes),
                    "response_types_supported": ["code"],
                    "grant_types_supported": ["authorization_code", "refresh_token"],
                    "code_challenge_methods_supported": ["S256"],
                    "token_endpoint_auth_methods_supported": ["none"],
                }
            )

        async def register(request: Request) -> Response:
            metadata = OAuthClientMetadata.model_validate(json.loads(await request.body()))
            info = self._register(metadata)
            return JSONResponse(info.model_dump(mode="json", exclude_none=True), status_code=201)

        async def token(request: Request) -> Response:
            form = {k: str(v) for k, v in (await request.form()).items()}
            try:
                if form.get("grant_type") == "authorization_code":
                    tokens = self._exchange(
                        form.get("client_id", ""),
                        form.get("code", ""),
                        form.get("code_verifier", ""),
                        form.get("redirect_uri", ""),
                    )
                elif form.get("grant_type") == "refresh_token":
                    tokens = self._refresh(form.get("client_id", ""), form.get("refresh_token", ""))
                else:
                    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            except OAuthRefused as refused:
                return JSONResponse({"error": refused.error}, status_code=400)
            return JSONResponse(tokens.model_dump(mode="json", exclude_none=True))

        return Starlette(
            routes=[
                Route(
                    f"/.well-known/oauth-protected-resource{resource_path}",
                    protected_resource,
                ),
                Route("/.well-known/oauth-authorization-server", server_metadata),
                Route("/register", register, methods=["POST"]),
                Route("/token", token, methods=["POST"]),
            ]
        )
