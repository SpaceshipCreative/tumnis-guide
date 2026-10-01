"""`FakeWebPush`: captures every push instead of sending it (P4-05)."""

from typing import Any

from tumnis.modules.notifications.adapters.port import PushPayload, PushResult, PushSubscription


class FakeWebPush:
    def __init__(self, **_deps: Any) -> None:
        self.sent: list[tuple[PushSubscription, PushPayload, int]] = []

    def script(self, endpoint: str, *statuses: int) -> None:
        raise NotImplementedError

    async def send(self, sub: PushSubscription, payload: PushPayload, ttl_s: int) -> PushResult:
        raise NotImplementedError
