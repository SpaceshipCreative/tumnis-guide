"""The SSRF guard: every outbound call to a user-configured host connects only to an address
it checked (P0-16, SEC-5).

`resolve_and_check` reads odd IPv4 literals the way the socket layer does (getaddrinfo), so
the blocked-address table runs on the system resolver; everything else scripts DNS with
`ScriptedResolver` and records requests with `httpx.MockTransport`, so nothing leaves the
process.
"""

from __future__ import annotations

from ipaddress import ip_network
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

PUBLIC = "93.184.216.34"

BLOCKED_HOSTS = (
    "127.0.0.1",
    "localhost",
    "2130706433",
    "0177.0.0.1",
    "0x7f.0.0.1",
    "127.1",
    "[::1]",
    "[::ffff:127.0.0.1]",
    "[::ffff:7f00:1]",
    "[::127.0.0.1]",
    "169.254.169.254",
    "[fd00:ec2::254]",
    "0.0.0.0",  # noqa: S104  # a hostile target, not a bind address
    "[::]",
    "224.0.0.1",
    "[fe80::1]",
    "[2002:7f00:1::]",
)


def _recorder() -> tuple[list[httpx.Request], httpx.MockTransport]:
    import httpx  # noqa: PLC0415

    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="ok")

    return seen, httpx.MockTransport(record)


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
@pytest.mark.parametrize("host", BLOCKED_HOSTS)
async def test_blocked_addresses_are_rejected(host: str) -> None:
    """T-P0-16-06
    Loopback in every spelling (decimal, octal, hex and short IPv4, IPv4-mapped,
    IPv4-compatible and 6to4 IPv6), link-local, cloud metadata, unspecified and multicast
    addresses all raise `SsrfBlocked`, in both deployment modes.
    """
    from tumnis.core.net import NetPolicy, SsrfBlocked, resolve_and_check  # noqa: PLC0415

    for mode in ("self-hosted", "hosted"):
        with pytest.raises(SsrfBlocked) as blocked:
            await resolve_and_check(host.strip("[]"), 443, NetPolicy(mode=mode))
        assert blocked.value.code == "ssrf_blocked"


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
async def test_dns_flip_still_connects_to_checked_address() -> None:
    """T-P0-16-07
    Given a resolver that answers 93.184.216.34 and then 127.0.0.1, when the guarded client
    GETs https://api.example.test/x, then the inner transport sees one request to
    https://93.184.216.34/x whose Host header and `sni_hostname` extension are the name,
    and the resolver was asked once.
    """
    from tumnis.core.net import NetPolicy, ScriptedResolver, guarded_client  # noqa: PLC0415

    resolver = ScriptedResolver([[PUBLIC], ["127.0.0.1"]])
    seen, inner = _recorder()
    policy = NetPolicy(mode="self-hosted")
    async with guarded_client(policy, timeout=5.0, resolver=resolver, inner=inner) as client:
        response = await client.get("https://api.example.test/x")

    assert response.status_code == 200
    assert len(seen) == 1
    (request,) = seen
    assert str(request.url) == f"https://{PUBLIC}/x"
    assert request.headers["host"] == "api.example.test"
    assert request.extensions["sni_hostname"] == "api.example.test"
    assert resolver.calls == [("api.example.test", 443)]


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
async def test_any_blocked_answer_rejects_the_host() -> None:
    """T-P0-16-08
    An answer set mixing a public and a loopback address ([93.184.216.34, 127.0.0.1]) is
    rejected, whichever comes first, and the guarded client sends nothing.
    """
    from tumnis.core.net import (  # noqa: PLC0415
        NetPolicy,
        ScriptedResolver,
        SsrfBlocked,
        guarded_client,
        resolve_and_check,
    )

    policy = NetPolicy(mode="self-hosted")
    for answer in ([PUBLIC, "127.0.0.1"], ["127.0.0.1", PUBLIC]):
        with pytest.raises(SsrfBlocked):
            await resolve_and_check("mixed.example.test", 443, policy, ScriptedResolver([answer]))

    seen, inner = _recorder()
    resolver = ScriptedResolver([[PUBLIC, "127.0.0.1"]])
    async with guarded_client(policy, timeout=5.0, resolver=resolver, inner=inner) as client:
        with pytest.raises(SsrfBlocked):
            await client.get("https://mixed.example.test/")
    assert seen == []


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
async def test_hosted_mode_rejects_private_ranges_unless_allowlisted() -> None:
    """T-P0-16-09
    Hosted: 10.1.2.3, 192.168.1.5 and 100.101.1.1 are rejected, and 10.1.2.3 is allowed once
    10.1.0.0/16 is allow-listed (192.168.1.5 still is not). Self-hosted: the private LAN is
    allowed, loopback is still rejected, and the allow-list never opens loopback.
    """
    from tumnis.core.net import NetPolicy, SsrfBlocked, resolve_and_check  # noqa: PLC0415

    hosted = NetPolicy(mode="hosted")
    for address in ("10.1.2.3", "192.168.1.5", "100.101.1.1", "fd12:3456::1"):
        with pytest.raises(SsrfBlocked):
            await resolve_and_check(address, 443, hosted)

    listed = NetPolicy(mode="hosted", allowlist=(ip_network("10.1.0.0/16"),))
    assert str(await resolve_and_check("10.1.2.3", 443, listed)) == "10.1.2.3"
    with pytest.raises(SsrfBlocked):
        await resolve_and_check("192.168.1.5", 443, listed)
    assert str(await resolve_and_check(PUBLIC, 443, hosted)) == PUBLIC

    self_hosted = NetPolicy(mode="self-hosted")
    for address in ("10.1.2.3", "192.168.1.5", "100.101.1.1", "172.20.0.4"):
        assert str(await resolve_and_check(address, 443, self_hosted)) == address
    with pytest.raises(SsrfBlocked):
        await resolve_and_check("127.0.0.1", 443, self_hosted)
    loopback_listed = NetPolicy(mode="hosted", allowlist=(ip_network("127.0.0.0/8"),))
    with pytest.raises(SsrfBlocked):
        await resolve_and_check("127.0.0.1", 443, loopback_listed)


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
async def test_redirects_are_rechecked_and_capped() -> None:
    """T-P0-16-10
    The guarded client never follows redirects on its own; `follow_redirects` follows up to
    five, re-checking each hop: a 302 to http://127.0.0.1/ raises `SsrfBlocked` without
    reaching it, and a chain of six redirects stops with `too_many_redirects`.
    """
    import httpx  # noqa: PLC0415

    from tumnis.core.net import (  # noqa: PLC0415
        MAX_REDIRECTS,
        NetPolicy,
        ScriptedResolver,
        SsrfBlocked,
        TooManyRedirects,
        follow_redirects,
        guarded_client,
    )

    assert MAX_REDIRECTS == 5
    seen: list[httpx.Request] = []

    def to_loopback(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(302, headers={"location": "http://127.0.0.1/"})

    policy = NetPolicy(mode="self-hosted")
    resolver = ScriptedResolver([[PUBLIC]])
    inner = httpx.MockTransport(to_loopback)
    async with guarded_client(policy, timeout=5.0, resolver=resolver, inner=inner) as client:
        assert client.follow_redirects is False
        assert (await client.get("https://hop.example.test/")).status_code == 302
        with pytest.raises(SsrfBlocked):
            await follow_redirects(client, client.build_request("GET", "https://hop.example.test/"))
    assert [str(r.url) for r in seen] == [f"https://{PUBLIC}/", f"https://{PUBLIC}/"]

    def chain(request: httpx.Request) -> httpx.Response:
        step = int(request.url.path.strip("/") or 0)
        if step < 6:
            return httpx.Response(302, headers={"location": f"/{step + 1}"})
        return httpx.Response(200, text="arrived")

    inner = httpx.MockTransport(chain)
    async with guarded_client(policy, timeout=5.0, resolver=resolver, inner=inner) as client:
        with pytest.raises(TooManyRedirects) as capped:
            await follow_redirects(client, client.build_request("GET", "https://hop.example.test/"))
        assert capped.value.code == "too_many_redirects"
        # Five hops are fine: /1 redirects to /6 in five steps.
        done = await follow_redirects(
            client, client.build_request("GET", "https://hop.example.test/1")
        )
        assert done.status_code == 200
        assert done.text == "arrived"


@pytest.mark.req("SEC-5")
@pytest.mark.wp("P0-16")
@pytest.mark.xfail(strict=True, reason="spec:P0-16")
async def test_unlisted_port_is_rejected() -> None:
    """T-P0-16-11
    https://example.com:5432/ raises `SsrfBlocked` before anything is resolved or sent; a
    port the adapter adds to its policy is allowed.
    """
    from tumnis.core.net import (  # noqa: PLC0415
        NetPolicy,
        ScriptedResolver,
        SsrfBlocked,
        guarded_client,
    )

    policy = NetPolicy(mode="self-hosted")
    assert policy.ports == frozenset({80, 443, 8080, 8443})
    resolver = ScriptedResolver([[PUBLIC]])
    seen, inner = _recorder()
    async with guarded_client(policy, timeout=5.0, resolver=resolver, inner=inner) as client:
        with pytest.raises(SsrfBlocked):
            await client.get("https://example.com:5432/")
    assert seen == []
    assert resolver.calls == []

    wider = NetPolicy(mode="self-hosted", ports=policy.ports | {5432})
    async with guarded_client(wider, timeout=5.0, resolver=resolver, inner=inner) as client:
        assert (await client.get("https://example.com:5432/")).status_code == 200
    assert str(seen[0].url) == f"https://{PUBLIC}:5432/"
