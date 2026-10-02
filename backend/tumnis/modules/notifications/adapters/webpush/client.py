"""`WebPushClient`: the real Web Push sender (P4-05, FR-8.3).

One push is one POST of the encrypted payload to the subscription's endpoint (the
browser vendor's push service):
- the payload is encrypted for the browser with `aes128gcm` (RFC 8291 over RFC 8188),
  through `http_ece`, the library pywebpush itself uses ([pywebpush `WebPusher.encode`]
  (https://github.com/web-push-libs/pywebpush));
- the request carries `TTL` (RFC 8030 section 5.2) and `Authorization: vapid t=<JWT>,k=<key>`
  signed with the workspace's VAPID key by `py_vapid.Vapid02` (RFC 8292), with the claims
  `aud` (the endpoint's origin), `exp` (12 hours ahead, on the injected clock) and `sub`;
- it goes through the SSRF-guarded client (`tumnis.core.net.guarded_client`), and only to an
  endpoint `rules.endpoint_allowed` accepts, checked before anything is encrypted or sent.
pywebpush's own sender is not used: it posts through `requests` or `aiohttp`, and every
outbound call here goes through `tumnis.core.net` (AGENTS.md).

Answers: 201 (or any 2xx) `sent`; 404 and 410 `gone` (the subscription expired or was
removed, RFC 8030 section 7.3); 429 and 5xx, timeouts and connection errors
`AdapterUnavailable`; any other answer `AdapterRejected`. One attempt per call
(`RetryPolicy(max_attempts=1)`): the delivery workflow retries and records each attempt.
"""

import base64
from typing import Final, Literal
from urllib.parse import urlsplit

import http_ece  # type: ignore[import-untyped]
import httpx
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid02  # type: ignore[import-untyped]

from tumnis.core.adapters.base import Adapter, AdapterRejected, AdapterUnavailable, CallPolicy
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock, SystemClock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.notifications import rules
from tumnis.modules.notifications.adapters.port import (
    PushPayload,
    PushResult,
    PushSubscription,
    VapidKey,
)

NAME: Final = "notifications.webpush"
TIMEOUT_S: Final = 10.0  # one push service call
VAPID_EXP_S: Final = 12 * 3600  # RFC 8292: no more than 24 hours ahead
GONE: Final = frozenset({404, 410})
TOO_MANY: Final = 429
SERVER_ERROR: Final = 500


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encrypt(plaintext: bytes, sub: PushSubscription) -> bytes:
    """The aes128gcm body for this browser: a fresh ephemeral key per message (RFC 8291)."""
    try:
        receiver = unb64url(sub.p256dh)
        auth = unb64url(sub.auth)
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver)
    except ValueError:
        raise AdapterRejected(NAME, "send", "subscription keys are not valid") from None
    sender = ec.generate_private_key(ec.SECP256R1())
    body: bytes = http_ece.encrypt(
        plaintext, private_key=sender, dh=receiver, auth_secret=auth, version="aes128gcm"
    )
    return body


class WebPushClient(Adapter):
    """One instance per workspace (its VAPID key), so one workspace's failing pushes do
    not open the breaker for another."""

    name = NAME

    def __init__(  # the key, the network and the test seams
        self,
        *,
        vapid: VapidKey,
        policy: NetPolicy | None = None,
        clock: Clock | None = None,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        """`policy` defaults to the hosted policy (public addresses only); the worker
        passes the deployment's. `transport` replaces the network (contract tests)."""
        self._clock = clock or SystemClock()
        super().__init__(
            policy=CallPolicy(timeout_s=timeout_s, retry=RetryPolicy(max_attempts=1)),
            clock=self._clock,
        )
        self._vapid = vapid
        self._signer = Vapid02.from_raw(vapid.private_key.encode())
        self._net = policy or NetPolicy(mode="hosted")
        self._resolver = resolver
        self._transport = transport
        self._timeout_s = timeout_s

    def claims(self, endpoint: str) -> dict[str, str | int]:
        parts = urlsplit(endpoint)
        return {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int(self._clock.now().timestamp()) + VAPID_EXP_S,
            "sub": self._vapid.subject,
        }

    async def send(self, sub: PushSubscription, payload: PushPayload, ttl_s: int) -> PushResult:
        op = "send"
        if not rules.endpoint_allowed(sub.endpoint):
            raise AdapterRejected(self.name, op, "endpoint is not a known push service")
        body = encrypt(payload.model_dump_json().encode(), sub)
        claims = self.claims(sub.endpoint)
        headers = {
            "Content-Type": "application/octet-stream",
            "Content-Encoding": "aes128gcm",
            "TTL": str(ttl_s),
            **self._signer.sign(claims),
        }

        async def post() -> PushResult:
            try:
                async with guarded_client(
                    self._net,
                    timeout=self._timeout_s,
                    resolver=self._resolver,
                    inner=self._transport,
                ) as http:
                    response = await http.post(sub.endpoint, content=body, headers=headers)
            except httpx.TransportError as exc:
                raise AdapterUnavailable(self.name, op, type(exc).__name__) from None
            status = response.status_code
            if status in GONE:
                outcome: Literal["sent", "gone"] = "gone"
            elif status == TOO_MANY or status >= SERVER_ERROR:
                retry_after = response.headers.get("Retry-After", "")
                raise AdapterUnavailable(
                    self.name,
                    op,
                    str(status),
                    retry_after_s=float(retry_after) if retry_after.isdigit() else None,
                )
            elif 200 <= status < 300:  # noqa: PLR2004
                outcome = "sent"
            else:
                raise AdapterRejected(self.name, op, str(status))
            return PushResult(
                outcome=outcome,
                status_code=status,
                encrypted_bytes=len(body),
                ttl_s=ttl_s,
                vapid_claims=claims,
            )

        return await self.call(op, post, idempotent=False)
