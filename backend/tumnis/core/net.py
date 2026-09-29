"""SSRF-guarded outbound HTTP with a pinned IP (P0-16, SEC-5). Interface only; the code
lands with its spec tests (T-P0-16-06 to 11)."""

from collections.abc import Sequence
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network
from typing import Final

import httpx

from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.types import DeploymentMode

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
MAX_REDIRECTS: Final = 5


@dataclass(frozen=True)
class NetPolicy:
    mode: DeploymentMode
    allowlist: tuple[IPNetwork, ...] = ()
    ports: frozenset[int] = frozenset({80, 443, 8080, 8443})


class SsrfBlocked(AdapterRejected):
    code = "ssrf_blocked"


class TooManyRedirects(AdapterRejected):
    code = "too_many_redirects"


class ScriptedResolver:
    def __init__(self, answers: Sequence[Sequence[str]]) -> None:
        self.answers = [list(answer) for answer in answers]
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        raise NotImplementedError


async def system_resolver(host: str, port: int) -> list[str]:
    raise NotImplementedError


async def resolve_and_check(
    host: str, port: int, policy: NetPolicy, resolver: object = system_resolver
) -> IPAddress:
    raise NotImplementedError


def guarded_client(
    policy: NetPolicy,
    *,
    timeout: float,
    resolver: object = system_resolver,
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    raise NotImplementedError


async def follow_redirects(
    client: httpx.AsyncClient, request: httpx.Request, *, max_redirects: int = MAX_REDIRECTS
) -> httpx.Response:
    raise NotImplementedError
