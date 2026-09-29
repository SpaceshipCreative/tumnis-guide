"""`CoolifyStatusApi`: the read-only Coolify REST client (P2-14, FR-12.2). Spec interface;
the client lands with the implementation."""

from typing import Any

import httpx

from tumnis.core.adapters.base import Adapter, AdapterRejected
from tumnis.core.adapters.registry import Health
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.coolify.adapters.port import TAKE_DEFAULT
from tumnis.modules.coolify.rules import ApplicationView, DeploymentView


class ReadOnlyViolation(AdapterRejected):
    """A method other than GET or HEAD was asked of the Coolify client; nothing was sent."""

    code = "read_only_violation"

    def __init__(self, method: str) -> None:
        super().__init__("coolify.status", "request", f"{method.upper()} is not a read")
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
        timeout_s: float,
    ) -> None:
        self._base_url = base_url

    async def request(
        self, method: str, path: str, *, params: dict[str, str] | None = None
    ) -> httpx.Response:
        raise NotImplementedError


class CoolifyStatusApi(Adapter):
    name = "coolify.status"

    def __init__(self, **kwargs: Any) -> None:
        raise NotImplementedError

    async def get_application(self, app_uuid: str) -> ApplicationView:
        raise NotImplementedError

    async def list_deployments(
        self, app_uuid: str, take: int = TAKE_DEFAULT
    ) -> list[DeploymentView]:
        raise NotImplementedError

    async def health(self) -> Health:
        raise NotImplementedError
