"""The Web Push port (P4-05, FR-8.3): send one encrypted push to one browser subscription.

`send` answers a `PushResult` for a push the service took (201) or a subscription that is
gone (404 or 410: the caller deletes it). Timeouts, 5xx and 429 raise `AdapterUnavailable`
(the delivery is retried); any other refusal raises `AdapterRejected`.
"""

from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import BaseModel

from tumnis.modules.notifications.rules import PushPayload

__all__ = ["PushPayload", "PushResult", "PushSubscription", "VapidKey", "WebPushAdapter"]


@dataclass(frozen=True)
class PushSubscription:
    """A browser's `PushSubscription` (W3C Push API): endpoint and its two keys, base64url."""

    endpoint: str
    p256dh: str
    auth: str


@dataclass(frozen=True)
class VapidKey:
    """The workspace's VAPID signing key (RFC 8292): the raw P-256 private value,
    base64url, and the `sub` contact claim (`mailto:` or `https:`)."""

    private_key: str
    subject: str


class PushResult(BaseModel):
    outcome: Literal["sent", "gone"]
    status_code: int
    encrypted_bytes: int  # the aes128gcm body sent
    ttl_s: int
    vapid_claims: dict[str, str | int]  # aud, exp, sub as signed


class WebPushAdapter(Protocol):
    async def send(self, sub: PushSubscription, payload: PushPayload, ttl_s: int) -> PushResult: ...
