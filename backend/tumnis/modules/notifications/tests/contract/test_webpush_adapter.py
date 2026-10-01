"""The Web Push port's contract (P4-05, FR-8.3): the fake and the real client behave the
same (AGENTS.md: fakes obey the real contract). The real client runs over an
`httpx.MockTransport` push service through the SSRF-guarded client, so no socket opens.

Every key here is generated when the test runs: the browser's P-256 subscription key and
auth secret, and the workspace's VAPID key. Nothing key-shaped is committed.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import FixedClock
from tumnis.core.net import NetPolicy
from tumnis.modules.notifications import rules
from tumnis.modules.notifications.adapters.port import (
    PushSubscription,
    VapidKey,
    WebPushAdapter,
)

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
ENDPOINT = "https://fcm.googleapis.com/fcm/send/contract-test-subscription"
SUBJECT = "mailto:owner@example.com"
TTL_S = 3600
ITEM = UUID("0199aa00-0000-7000-8000-0000000004a7")
PUSH_ADDRESS = "192.168.50.20"  # what the scripted resolver answers (self-hosted policy)


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


@dataclass
class Browser:
    """A browser's subscription, with the private half kept to decrypt what it gets."""

    key: ec.EllipticCurvePrivateKey
    auth: bytes

    @classmethod
    def new(cls) -> Browser:
        return cls(ec.generate_private_key(ec.SECP256R1()), os.urandom(16))

    def subscription(self, endpoint: str = ENDPOINT) -> PushSubscription:
        public = self.key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        return PushSubscription(endpoint=endpoint, p256dh=b64url(public), auth=b64url(self.auth))


def new_vapid() -> tuple[VapidKey, ec.EllipticCurvePublicKey]:
    key = ec.generate_private_key(ec.SECP256R1())
    raw = key.private_numbers().private_value.to_bytes(32, "big")
    return VapidKey(private_key=b64url(raw), subject=SUBJECT), key.public_key()


def approval_payload() -> rules.PushPayload:
    return rules.push_payload(
        rules.ReviewItemLite(id=ITEM, kind="approval", project_name="Acme site")
    )


class WebPushContract(AdapterContract[WebPushAdapter]):
    port, adapter_name = WebPushAdapter, "notifications.webpush"

    @pytest.fixture
    def script(self) -> Callable[[str, int], None]:
        """Script the push service's next answer for an endpoint."""
        raise NotImplementedError

    async def test_contract(self, subject: WebPushAdapter) -> None:
        """T-P4-05-07
        A push the service takes: 201 `sent`, the encrypted body under the 4 KB bound and
        exactly the aes128gcm size of the payload, the TTL passed through, and VAPID claims
        present (`aud` the endpoint's origin, `sub` a contact, `exp` within 24 hours).
        """
        payload = approval_payload()
        result = await subject.send(Browser.new().subscription(), payload, TTL_S)
        assert (result.outcome, result.status_code) == ("sent", 201)
        plaintext = len(payload.model_dump_json().encode())
        assert result.encrypted_bytes == rules.encrypted_size(plaintext)
        assert result.encrypted_bytes < rules.MAX_PUSH_BYTES
        assert result.ttl_s == TTL_S
        claims = result.vapid_claims
        assert claims["aud"] == "https://fcm.googleapis.com"
        assert str(claims["sub"]).startswith(("mailto:", "https://"))
        assert 0 < int(claims["exp"]) - int(T0.timestamp()) <= 24 * 3600

    @pytest.mark.parametrize("status", [404, 410])
    async def test_gone_subscription_answers_gone(
        self, subject: WebPushAdapter, script: Callable[[str, int], None], status: int
    ) -> None:
        """T-P4-05-07
        404 and 410 mean the subscription is gone: `gone`, not an error."""
        script(ENDPOINT, status)
        result = await subject.send(Browser.new().subscription(), approval_payload(), TTL_S)
        assert (result.outcome, result.status_code) == ("gone", status)

    @pytest.mark.parametrize("status", [429, 500, 503])
    async def test_service_trouble_is_unavailable(
        self, subject: WebPushAdapter, script: Callable[[str, int], None], status: int
    ) -> None:
        """T-P4-05-07
        429 and 5xx are `AdapterUnavailable` (the delivery retries)."""
        script(ENDPOINT, status)
        with pytest.raises(AdapterUnavailable):
            await subject.send(Browser.new().subscription(), approval_payload(), TTL_S)

    async def test_other_refusals_are_rejected(
        self, subject: WebPushAdapter, script: Callable[[str, int], None]
    ) -> None:
        """T-P4-05-07
        Any other 4xx is `AdapterRejected` (not retried)."""
        script(ENDPOINT, 413)
        with pytest.raises(AdapterRejected):
            await subject.send(Browser.new().subscription(), approval_payload(), TTL_S)

    @pytest.mark.parametrize(
        "endpoint", ["http://fcm.googleapis.com/fcm/send/x", "https://push.example.com/x"]
    )
    async def test_endpoint_outside_the_allow_list_is_refused(
        self, subject: WebPushAdapter, endpoint: str
    ) -> None:
        """T-P4-05-07
        An endpoint outside the push-service allow-list is refused before anything is
        sent (SEC-5)."""
        with pytest.raises(AdapterRejected):
            await subject.send(Browser.new().subscription(endpoint), approval_payload(), TTL_S)


@pytest.mark.contract
@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
class TestWebPushFake(WebPushContract):
    impl = "fake"

    @pytest.fixture
    def fake(self) -> Any:
        from tumnis.modules.notifications.adapters.webpush.fake import (  # noqa: PLC0415
            FakeWebPush,
        )

        vapid, _public = new_vapid()
        return FakeWebPush(vapid=vapid, clock=FixedClock(T0))

    @pytest.fixture
    def subject(self, fake: Any) -> WebPushAdapter:
        adapter: WebPushAdapter = fake
        return adapter

    @pytest.fixture
    def script(self, fake: Any) -> Callable[[str, int], None]:
        script: Callable[[str, int], None] = fake.script
        return script


class PushService:
    """A push service as an `httpx.MockTransport` handler: 201 unless scripted."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.answers: dict[str, list[int]] = {}

    def script(self, endpoint: str, status: int) -> None:
        self.answers.setdefault(endpoint, []).append(status)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = f"https://{request.headers['host']}{request.url.raw_path.decode()}"
        queued = self.answers.get(url, [])
        return httpx.Response(queued.pop(0) if queued else 201)


async def _push_address(host: str, port: int) -> list[str]:
    return [PUSH_ADDRESS]


@pytest.mark.contract
@pytest.mark.req("FR-8.3")
@pytest.mark.wp("P4-05")
class TestWebPushReal(WebPushContract):
    impl = "real"

    @pytest.fixture
    def service(self) -> PushService:
        return PushService()

    @pytest.fixture
    def vapid(self) -> tuple[VapidKey, ec.EllipticCurvePublicKey]:
        return new_vapid()

    @pytest.fixture
    def subject(
        self, service: PushService, vapid: tuple[VapidKey, ec.EllipticCurvePublicKey]
    ) -> WebPushAdapter:
        from tumnis.modules.notifications.adapters.webpush.client import (  # noqa: PLC0415
            WebPushClient,
        )

        adapter: WebPushAdapter = WebPushClient(
            vapid=vapid[0],
            policy=NetPolicy(mode="self-hosted"),
            clock=FixedClock(T0),
            resolver=_push_address,
            transport=httpx.MockTransport(service.handle),
        )
        return adapter

    @pytest.fixture
    def script(self, service: PushService) -> Callable[[str, int], None]:
        return service.script

    async def test_request_decrypts_and_carries_ttl_and_a_valid_vapid_signature(
        self,
        subject: WebPushAdapter,
        service: PushService,
        vapid: tuple[VapidKey, ec.EllipticCurvePublicKey],
    ) -> None:
        """T-P4-05-07
        On the wire: one POST to the endpoint with `Content-Encoding: aes128gcm`, `TTL`,
        and `Authorization: vapid t=<jwt>, k=<key>` whose ES256 signature verifies with the
        workspace's public key; the body decrypts with the browser's key to the payload
        (RFC 8030, RFC 8188, RFC 8291, RFC 8292)."""
        import http_ece  # type: ignore[import-untyped]  # noqa: PLC0415

        browser = Browser.new()
        payload = approval_payload()
        await subject.send(browser.subscription(), payload, TTL_S)
        (request,) = service.requests
        assert request.method == "POST"
        assert request.headers["host"] == "fcm.googleapis.com"
        assert request.headers["content-encoding"] == "aes128gcm"
        assert request.headers["ttl"] == str(TTL_S)
        plain = http_ece.decrypt(
            request.content, private_key=browser.key, auth_secret=browser.auth, version="aes128gcm"
        )
        assert plain == payload.model_dump_json().encode()

        scheme, _, params = request.headers["authorization"].partition(" ")
        assert scheme == "vapid"
        fields = dict(part.strip().split("=", 1) for part in params.split(","))
        public = vapid[1].public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        assert unb64url(fields["k"]) == public
        head, body, signature = fields["t"].split(".")
        assert json.loads(unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
        claims = json.loads(unb64url(body))
        assert claims["aud"] == "https://fcm.googleapis.com"
        assert claims["sub"] == SUBJECT
        raw = unb64url(signature)
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        vapid[1].verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
