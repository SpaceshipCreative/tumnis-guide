"""`WebPushClient`: the real Web Push sender (P4-05)."""

from typing import Any

from tumnis.modules.notifications.adapters.port import PushPayload, PushResult, PushSubscription


class WebPushClient:
    def __init__(self, **_deps: Any) -> None:
        pass

    async def send(self, sub: PushSubscription, payload: PushPayload, ttl_s: int) -> PushResult:
        raise NotImplementedError
