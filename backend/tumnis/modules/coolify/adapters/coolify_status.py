"""`CoolifyStatusApi`: the read-only Coolify REST client (P2-14, FR-12.2).

- `GET {base}/api/v1/applications/{uuid}`: the application (name, domains, preview URL
  template);
- `GET {base}/api/v1/deployments/applications/{uuid}?take=10`: its newest deployments
  (Coolify v4.4 answers `{count, deployments}`; a bare list is accepted too), with
  `Authorization: Bearer <token>`
  ([list deployments by app](https://coolify.io/docs/api-reference/api/operations/list-deployments-by-app-uuid)).

Every request goes through `ReadOnlyHttp`, which refuses any method but GET and HEAD before
resolving a host, over the SSRF-guarded client (`tumnis.core.net.guarded_client`; Coolify's
own port 8000 is added to the policy's ports). The poll runs inside a DBOS step and polls
again five minutes later, so a call makes one attempt (`RetryPolicy(max_attempts=1)`).
5xx and 429 are `AdapterUnavailable`, other non-2xx answers and an answer of an unexpected
shape `AdapterRejected`. The token never reaches a log line or an error message.
"""

from dataclasses import replace
from typing import Any, Final
from urllib.parse import quote

import httpx
from pydantic import ValidationError

from tumnis.core.adapters.base import (
    Adapter,
    AdapterRejected,
    AdapterUnavailable,
    CallPolicy,
)
from tumnis.core.adapters.registry import Health
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.coolify.adapters.port import TAKE_DEFAULT
from tumnis.modules.coolify.rules import ApplicationView, DeploymentView

NAME: Final = "coolify.status"
READ_METHODS: Final = frozenset({"GET", "HEAD"})
COOLIFY_PORT: Final = 8000  # Coolify's dashboard and API port on a plain install
TIMEOUT_S: Final = 10.0  # plan default for one Coolify call


class ReadOnlyViolation(AdapterRejected):
    """A method other than GET or HEAD was asked of the Coolify client; nothing was sent."""

    code = "read_only_violation"

    def __init__(self, method: str) -> None:
        super().__init__(NAME, "request", f"{method.upper()} is not a read")
        self.method = method


class ReadOnlyHttp:
    """Wraps the SSRF-guarded client; request() raises ReadOnlyViolation for any method but
    GET/HEAD."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        policy: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._policy = replace(policy, ports=policy.ports | {COOLIFY_PORT})
        self._resolver = resolver
        self._transport = transport
        self._timeout_s = timeout_s

    async def request(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        verb = method.upper()
        if verb not in READ_METHODS:
            raise ReadOnlyViolation(method)  # before any resolve or connect
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
        async with guarded_client(
            self._policy, timeout=self._timeout_s, resolver=self._resolver, inner=self._transport
        ) as http:
            return await http.request(
                verb, f"{self._base_url}{path}", params=params, headers=headers
            )


class CoolifyStatusApi(Adapter):
    """One instance per workspace connection (base URL and token), so one workspace's
    broken token does not open the breaker for another."""

    name = NAME

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        policy: NetPolicy | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Clock | None = None,
    ) -> None:
        """`policy` defaults to the hosted policy (public addresses only); the worker
        passes the deployment's (`Settings.net_policy()`: self-hosted reaches the LAN).
        `transport` replaces the network (the recorded replay)."""
        super().__init__(
            policy=CallPolicy(timeout_s=TIMEOUT_S, retry=RetryPolicy(max_attempts=1)),
            clock=clock or SystemClock(),
        )
        self._http = ReadOnlyHttp(
            base_url=base_url,
            token=token,
            policy=policy or NetPolicy(mode="hosted"),
            resolver=resolver,
            transport=transport,
        )

    async def get_application(self, app_uuid: str) -> ApplicationView:
        op = "get_application"

        async def fetch() -> ApplicationView:
            body = await self._get(op, f"/api/v1/applications/{_segment(app_uuid)}")
            return self._parse(op, ApplicationView, body)

        return await self.call(op, fetch, idempotent=True)

    async def list_deployments(
        self, app_uuid: str, take: int = TAKE_DEFAULT
    ) -> list[DeploymentView]:
        op = "list_deployments"

        async def fetch() -> list[DeploymentView]:
            body = await self._get(
                op,
                f"/api/v1/deployments/applications/{_segment(app_uuid)}",
                params={"take": str(take)},
            )
            items = body.get("deployments") if isinstance(body, dict) else body
            if not isinstance(items, list):
                raise AdapterRejected(self.name, op, "unexpected response shape")
            return [self._parse(op, DeploymentView, item) for item in items]

        return await self.call(op, fetch, idempotent=True)

    async def health(self) -> Health:
        """Degraded while the breaker is open (recent calls failed); no call of its own."""
        return self.health_state()

    async def _get(self, op: str, path: str, params: dict[str, str] | None = None) -> Any:
        try:
            response = await self._http.request("GET", path, params=params)
        except httpx.TransportError as exc:
            raise AdapterUnavailable(self.name, op, type(exc).__name__) from None
        status = response.status_code
        if status == 429 or status >= 500:  # noqa: PLR2004
            retry_after = response.headers.get("Retry-After", "")
            raise AdapterUnavailable(
                self.name,
                op,
                str(status),
                retry_after_s=float(retry_after) if retry_after.isdigit() else None,
            )
        if not 200 <= status < 300:  # noqa: PLR2004  # a redirect is not followed either
            raise AdapterRejected(self.name, op, str(status))
        try:
            return response.json()
        except ValueError:
            raise AdapterRejected(self.name, op, "not JSON") from None

    def _parse[M: (ApplicationView, DeploymentView)](self, op: str, model: type[M], body: Any) -> M:
        try:
            return model.model_validate(body)
        except ValidationError:
            # Field names only through the adapter error: the input may hold anything.
            raise AdapterRejected(self.name, op, "unexpected response shape") from None


def _segment(value: str) -> str:
    """One path segment: a UUID cannot reach another endpoint."""
    return quote(value, safe="")
