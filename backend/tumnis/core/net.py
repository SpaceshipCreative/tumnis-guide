"""SSRF-guarded outbound HTTP with a pinned IP (P0-16, SEC-5).

Every adapter that calls a user-configured URL gets its client from `guarded_client`; the
`tumnis-raw-httpx` Semgrep rule forbids building an `httpx` client anywhere else. For each
request the client resolves the host once, checks every address it got (and any IPv4
address embedded in an IPv6 one), then connects to the first of them: the TLS handshake
still verifies the certificate against the name (httpx's `sni_hostname` extension), so a
DNS answer that changes between the check and the connect cannot redirect the call.
Redirects are never followed by the client; `follow_redirects` follows up to
MAX_REDIRECTS, and each hop goes through the same check.

Non-HTTP clients (asyncssh for SFTP, aioboto3 endpoint URLs) call `resolve_and_check`
themselves and connect to the address it returns.

Blocked in every deployment mode: loopback, unspecified, link-local, multicast, reserved
and cloud metadata addresses; nothing Tumnis talks to lives on its own loopback. Private
ranges (RFC 1918, CGNAT, IPv6 unique local) are allowed when self-hosted, because Inbox
Zero, Coolify and Hermes live on the homelab LAN (FR-12); hosted mode refuses them unless
OUTBOUND_ALLOWLIST lists the range.
"""

import asyncio
import re
import socket
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from ipaddress import (
    IPv4Address,
    IPv4Network,
    IPv6Address,
    IPv6Network,
    ip_address,
    ip_network,
)
from typing import Final

import httpx

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.types import DeploymentMode

IPAddress = IPv4Address | IPv6Address
IPNetwork = IPv4Network | IPv6Network
# (host, port) -> the addresses the name resolves to, as strings.
Resolver = Callable[[str, int], Awaitable[Sequence[str]]]

ADAPTER: Final = "core.net"
MAX_REDIRECTS: Final = 5  # plan default
DEFAULT_PORTS: Final = frozenset({80, 443, 8080, 8443})  # plan default; adapters may add theirs
SCHEME_PORTS: Final = {"http": 80, "https": 443}

ALWAYS_BLOCKED: Final[tuple[IPNetwork, ...]] = tuple(
    ip_network(n)
    for n in (
        "0.0.0.0/8",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "255.255.255.255/32",
        "100.100.100.200/32",  # Alibaba metadata
        "::/128",
        "::1/128",
        "fe80::/10",
        "ff00::/8",
        "fd00:ec2::254/128",  # AWS IPv6 metadata
    )
)
PRIVATE: Final[tuple[IPNetwork, ...]] = tuple(
    ip_network(n)
    for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7")
)
# What inet_aton reads as an IPv4 address: 1 to 4 dot-separated decimal, octal (0177) or
# hex (0x7f) parts, the last filling the remaining bytes (127.1, 2130706433).
_IPV4_NUMERIC: Final = re.compile(
    r"^(0[xX][0-9a-fA-F]*|[0-9]+)(\.(0[xX][0-9a-fA-F]*|[0-9]+)){0,3}\.?$"
)
_IPV4_COMPATIBLE: Final = ip_network("::/96")  # ::a.b.c.d (deprecated, still routed by some)
_NAT64: Final = ip_network("64:ff9b::/96")  # well-known NAT64 prefix


@dataclass(frozen=True)
class NetPolicy:
    mode: DeploymentMode
    allowlist: tuple[IPNetwork, ...] = ()  # hosted: private ranges explicitly allowed
    ports: frozenset[int] = DEFAULT_PORTS


class SsrfBlocked(AdapterRejected):
    """The destination is not one Tumnis may call; nothing was sent."""

    code = "ssrf_blocked"

    def __init__(self, host: str, reason: str) -> None:
        super().__init__(ADAPTER, "connect", f"{host}: {reason}")
        self.host = host
        self.reason = reason


class TooManyRedirects(AdapterRejected):
    code = "too_many_redirects"

    def __init__(self, url: str, max_redirects: int) -> None:
        super().__init__(ADAPTER, "redirect", f"{url}: more than {max_redirects} redirects")


def parse_allowlist(entries: Iterable[str]) -> tuple[IPNetwork, ...]:
    """OUTBOUND_ALLOWLIST entries (CIDR ranges or single addresses) as networks; ValueError
    on anything else. Host names are not accepted: only an address can be pinned."""
    return tuple(ip_network(entry.strip(), strict=False) for entry in entries if entry.strip())


def _embedded(ip: IPv6Address) -> list[IPv4Address]:
    found: list[IPv4Address] = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)  # (server, client)
    if ip in _NAT64 or ip in _IPV4_COMPATIBLE:
        found.append(IPv4Address(int(ip) & 0xFFFF_FFFF))
    return found


def embedded_v4(ip: IPv6Address) -> IPv4Address | None:
    """IPv4-mapped (::ffff:a.b.c.d), IPv4-compatible (::a.b.c.d), 6to4 (2002::/16) and Teredo
    forms (the Teredo client address), and the NAT64 prefix 64:ff9b::/96."""
    if ip.teredo is not None:
        return ip.teredo[1]
    found = _embedded(ip)
    return found[0] if found else None


def check_ip(ip: IPAddress, policy: NetPolicy) -> None:
    """Raise SsrfBlocked when `ip`, or an IPv4 address embedded in it, is blocked."""
    candidates: list[IPAddress] = [ip]
    if isinstance(ip, IPv6Address):
        candidates.extend(_embedded(ip))
    for candidate in candidates:
        if any(candidate in network for network in ALWAYS_BLOCKED):
            raise SsrfBlocked(str(ip), f"{candidate} is in a blocked range")
        if (
            policy.mode == "hosted"
            and any(candidate in network for network in PRIVATE)
            and not any(candidate in network for network in policy.allowlist)
        ):
            raise SsrfBlocked(str(ip), f"{candidate} is private and not allow-listed")


async def system_resolver(host: str, port: int) -> list[str]:
    """getaddrinfo, as the socket layer would resolve `host` (so decimal, octal, hex and
    short IPv4 literals become the address a connect would reach)."""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise AdapterUnavailable(ADAPTER, "resolve", f"{host}: {exc.strerror}") from exc
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


class ScriptedResolver:
    """A resolver for tests: each call returns the next scripted answer (the last one
    repeats) and is recorded in `calls` as (host, port)."""

    def __init__(self, answers: Sequence[Sequence[str]]) -> None:
        if not answers:
            raise ValueError("script at least one answer")
        self.answers = [list(answer) for answer in answers]
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int) -> list[str]:
        index = min(len(self.calls), len(self.answers) - 1)
        self.calls.append((host, port))
        return list(self.answers[index])


def _literal(name: str) -> IPAddress | None:
    """`name` as an address when it is one: IPv4 and IPv6 in standard form, and the IPv4
    spellings the socket layer also reads (decimal, octal, hex and short forms, through
    inet_aton, as getaddrinfo does for a numeric host)."""
    try:
        return ip_address(name)
    except ValueError:
        pass
    if _IPV4_NUMERIC.match(name):
        try:
            return IPv4Address(socket.inet_aton(name.rstrip(".")))
        except OSError:
            return None
    return None


def _is_localhost(name: str) -> bool:
    """RFC 6761: `localhost` and every name under it are loopback, whatever DNS says."""
    label = name.rstrip(".").lower()
    return label == "localhost" or label.endswith(".localhost")


async def resolve_and_check(
    host: str, port: int, policy: NetPolicy, resolver: Resolver = system_resolver
) -> IPAddress:
    """Resolves with getaddrinfo (so decimal, octal, hex and short IPv4 literals become real
    addresses, the same way the socket layer would read them), checks EVERY returned
    address plus its embedded IPv4, rejects if any is blocked, and returns the first
    address. The caller must connect to that address."""
    if port not in policy.ports:
        raise SsrfBlocked(host, f"port {port} is not allowed")
    name = host.removeprefix("[").removesuffix("]")
    literal = _literal(name)
    if literal is not None:
        check_ip(literal, policy)
        return literal
    if _is_localhost(name):
        raise SsrfBlocked(host, "localhost is always loopback")
    answers = await resolver(name, port)
    if not answers:
        raise SsrfBlocked(host, "resolves to no address")
    addresses = [ip_address(answer) for answer in answers]
    for address in addresses:
        check_ip(address, policy)
    return addresses[0]


class PinnedTransport(httpx.AsyncBaseTransport):
    """For each request: resolve_and_check(host), rewrite the URL to the IP, keep the Host
    header, set extensions['sni_hostname'] to the original host so TLS still verifies the
    name, then delegate."""

    def __init__(
        self, policy: NetPolicy, resolver: Resolver, inner: httpx.AsyncBaseTransport
    ) -> None:
        self.policy = policy
        self.resolver = resolver
        self.inner = inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.scheme not in SCHEME_PORTS:
            raise SsrfBlocked(url.host or str(url), f"scheme {url.scheme!r} is not allowed")
        port = url.port or SCHEME_PORTS[url.scheme]
        address = await resolve_and_check(url.host, port, self.policy, self.resolver)
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=str(address)),
            headers=request.headers,  # keeps Host: the name, not the address
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": url.host},
        )
        return await self.inner.handle_async_request(pinned)

    async def aclose(self) -> None:
        await self.inner.aclose()


def guarded_client(
    policy: NetPolicy,
    *,
    timeout: float,
    resolver: Resolver = system_resolver,
    inner: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """follow_redirects=False (`follow_redirects` below follows up to 5 (plan default)
    redirects, re-checking each hop), trust_env=False (no proxy variables),
    transport=PinnedTransport(...)."""
    transport = PinnedTransport(
        policy, resolver, inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)
    )
    return httpx.AsyncClient(
        transport=transport, timeout=timeout, follow_redirects=False, trust_env=False
    )


async def follow_redirects(
    client: httpx.AsyncClient, request: httpx.Request, *, max_redirects: int = MAX_REDIRECTS
) -> httpx.Response:
    """Send `request` on a guarded client and follow up to `max_redirects` redirects; each
    hop goes through the client's transport, so each is resolved and checked again."""
    response = await client.send(request)
    hops = 0
    while response.next_request is not None:
        if hops >= max_redirects:
            await response.aclose()
            raise TooManyRedirects(str(request.url), max_redirects)
        hops += 1
        following = response.next_request
        await response.aclose()
        response = await client.send(following)
    return response
