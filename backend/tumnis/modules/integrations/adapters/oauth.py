"""`McpOAuthClient`: the OAuth port (`integrations.oauth`) over HTTP (P3-02).

What connecting an MCP provider needs from its authorization server, per the MCP
authorization spec: protected-resource metadata (RFC 9728) names the authorization
server, whose metadata (RFC 8414) names its endpoints; dynamic client registration
(RFC 7591) registers Tumnis as a public client; the authorization code is exchanged with
the PKCE verifier (RFC 7636) and the resource indicator (RFC 8707); refresh tokens rotate.

Discovery order and parsing are the MCP SDK's helpers (`mcp.client.auth.utils`, SDK
1.30.0), so they follow the SDK's reading of the spec; the HTTP goes through
`core.net.guarded_client` (SSRF checks on every request, no redirects, no proxy
variables). A refused grant (HTTP 400/401 with an OAuth `error`) raises `OAuthRefused`;
an unreachable server, a 5xx, a 408 or a 429 raises `AdapterUnavailable`; any other
non-200 answer raises `AdapterRejected`.
"""

import re
from typing import Final

import httpx
from mcp.client.auth import OAuthFlowError
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
    handle_registration_response,
    validate_metadata_issuer,
)
from mcp.shared.auth import (
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
    ProtectedResourceMetadata,
)
from pydantic import ValidationError

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.adapters.registry import Health
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.integrations.oauth_port import ADAPTER, OAuthRefused, OAuthServer

TIMEOUT_S: Final = 15.0  # plan default for one authorization server call
_ERROR_CODE: Final = re.compile(r"[a-z_]{1,64}")  # RFC 6749 error codes
# A token endpoint refuses a grant with 400 or 401 (RFC 6749 section 5.2): only that means
# signing in again. A timeout or rate limit is transient and is retried with the grant kept.
_REFUSED: Final = frozenset({400, 401})
_TRANSIENT: Final = frozenset({408, 429})


class McpOAuthClient:
    def __init__(
        self,
        *,
        policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        """`policy` defaults to the hosted policy (public addresses only); the worker
        passes the deployment's. `transport` replaces the network (contract tests)."""
        self._policy = policy or NetPolicy(mode="hosted")
        self._resolver = resolver
        self._transport = transport
        self._timeout_s = timeout_s

    def _http(self) -> httpx.AsyncClient:
        return guarded_client(
            self._policy, timeout=self._timeout_s, resolver=self._resolver, inner=self._transport
        )

    async def _send(
        self, http: httpx.AsyncClient, op: str, request: httpx.Request
    ) -> httpx.Response:
        try:
            response = await http.send(request)
        except httpx.HTTPError as exc:
            raise AdapterUnavailable(ADAPTER, op, type(exc).__name__) from None
        if response.status_code >= 500 or response.status_code in _TRANSIENT:  # noqa: PLR2004
            raise AdapterUnavailable(ADAPTER, op, f"HTTP {response.status_code}")
        return response

    async def discover(self, server_url: str) -> OAuthServer:
        op = "discover"
        async with self._http() as http:
            resource: ProtectedResourceMetadata | None = None
            for url in build_protected_resource_metadata_discovery_urls(None, server_url):
                found = await self._send(http, op, create_oauth_metadata_request(url))
                resource = await handle_protected_resource_response(found)
                if resource is not None:
                    break
            issuer = str(resource.authorization_servers[0]) if resource else None
            metadata: OAuthMetadata | None = None
            for url in build_oauth_authorization_server_metadata_discovery_urls(issuer, server_url):
                found = await self._send(http, op, create_oauth_metadata_request(url))
                keep_trying, metadata = await handle_auth_metadata_response(found)
                if metadata is not None or not keep_trying:
                    break
        if metadata is None:
            raise AdapterRejected(ADAPTER, op, "no authorization server metadata")
        if issuer is not None:
            try:
                validate_metadata_issuer(metadata, issuer)
            except OAuthFlowError:
                raise AdapterRejected(ADAPTER, op, "issuer mismatch") from None
        scopes = (resource.scopes_supported if resource else None) or metadata.scopes_supported
        return OAuthServer(
            resource=server_url,
            issuer=str(metadata.issuer),
            authorization_endpoint=str(metadata.authorization_endpoint),
            token_endpoint=str(metadata.token_endpoint),
            registration_endpoint=(
                str(metadata.registration_endpoint) if metadata.registration_endpoint else None
            ),
            scopes=list(scopes or []),
        )

    async def register(
        self, server: OAuthServer, metadata: OAuthClientMetadata
    ) -> OAuthClientInformationFull:
        op = "register"
        if server.registration_endpoint is None:
            raise AdapterRejected(ADAPTER, op, "the server offers no dynamic registration")
        request = httpx.Request(
            "POST",
            server.registration_endpoint,
            json=metadata.model_dump(by_alias=True, mode="json", exclude_none=True),
        )
        async with self._http() as http:
            response = await self._send(http, op, request)
            try:
                return await handle_registration_response(response)
            except OAuthFlowError:
                raise AdapterRejected(ADAPTER, op, f"HTTP {response.status_code}") from None

    async def exchange(
        self,
        server: OAuthServer,
        client: OAuthClientInformationFull,
        *,
        code: str,
        code_verifier: str,
        redirect_uri: str,
    ) -> OAuthToken:
        return await self._token(
            "exchange",
            server,
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": redirect_uri,
                "client_id": client.client_id or "",
                "resource": server.resource,
            },
        )

    async def refresh(
        self, server: OAuthServer, client: OAuthClientInformationFull, *, refresh_token: str
    ) -> OAuthToken:
        return await self._token(
            "refresh",
            server,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client.client_id or "",
                "resource": server.resource,
            },
        )

    async def _token(self, op: str, server: OAuthServer, form: dict[str, str]) -> OAuthToken:
        request = httpx.Request("POST", server.token_endpoint, data=form)
        async with self._http() as http:
            response = await self._send(http, op, request)
            body = await response.aread()
        if response.status_code in _REFUSED:  # RFC 6749 section 5.2
            raise OAuthRefused(op, _oauth_error(body, response.status_code))
        if response.status_code != 200:  # noqa: PLR2004
            raise AdapterRejected(ADAPTER, op, f"HTTP {response.status_code}")
        try:
            return OAuthToken.model_validate_json(body)
        except ValidationError:
            raise AdapterRejected(ADAPTER, op, "invalid token response") from None

    async def health(self) -> Health:
        return "ok"


def _oauth_error(body: bytes, status: int) -> str:
    """The OAuth `error` code of a refused token request (RFC 6749 section 5.2); never the
    description, which may echo what was sent."""
    try:
        error = httpx.Response(status, content=body).json().get("error")
    except ValueError:
        error = None
    return error if isinstance(error, str) and _ERROR_CODE.fullmatch(error) else f"http_{status}"
