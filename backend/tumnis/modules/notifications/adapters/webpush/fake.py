"""`FakeWebPush`: a push service that captures every push instead of sending it (P4-05).

It answers as the real client does (the contract suite runs both): an endpoint outside the
push-service allow-list is refused before anything is "sent"; otherwise the next scripted
status for the endpoint (201 when none) is answered the same way, 2xx `sent`, 404/410
`gone`, 429/5xx `AdapterUnavailable`, anything else `AdapterRejected`. The result carries
the aes128gcm size the real body would have, the TTL and the VAPID claims the real client
signs.

Scripting: `script(endpoint, *statuses)` queues answers; `sent` holds (subscription,
payload, ttl) for every push answered 2xx; `calls` holds every attempt.
"""

from collections import defaultdict, deque
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.core.clock import Clock, SystemClock
from tumnis.modules.notifications import rules
from tumnis.modules.notifications.adapters.port import (
    PushPayload,
    PushResult,
    PushSubscription,
    VapidKey,
)

NAME: Final = "notifications.webpush"
VAPID_EXP_S: Final = 12 * 3600
DEFAULT_SUBJECT: Final = "mailto:push@example.com"


class FakeWebPush:
    def __init__(
        self, *, vapid: VapidKey | None = None, clock: Clock | None = None, **_deps: Any
    ) -> None:
        """Takes the real client's arguments; uses only the VAPID subject and the clock."""
        self._subject = vapid.subject if vapid else DEFAULT_SUBJECT
        self._clock = clock or SystemClock()
        self._answers: defaultdict[str, deque[int]] = defaultdict(deque)
        self.sent: list[tuple[PushSubscription, PushPayload, int]] = []
        self.calls: list[tuple[PushSubscription, PushPayload, int]] = []

    def script(self, endpoint: str, *statuses: int) -> None:
        self._answers[endpoint].extend(statuses)

    async def send(self, sub: PushSubscription, payload: PushPayload, ttl_s: int) -> PushResult:
        if not rules.endpoint_allowed(sub.endpoint):
            raise AdapterRejected(NAME, "send", "endpoint is not a known push service")
        self.calls.append((sub, payload, ttl_s))
        queued = self._answers.get(sub.endpoint)
        status = queued.popleft() if queued else 201
        outcome: Literal["sent", "gone"]
        if status in {404, 410}:
            outcome = "gone"
        elif status == 429 or status >= 500:  # noqa: PLR2004
            raise AdapterUnavailable(NAME, "send", str(status))
        elif 200 <= status < 300:  # noqa: PLR2004
            outcome = "sent"
            self.sent.append((sub, payload, ttl_s))
        else:
            raise AdapterRejected(NAME, "send", str(status))
        parts = urlsplit(sub.endpoint)
        return PushResult(
            outcome=outcome,
            status_code=status,
            encrypted_bytes=rules.encrypted_size(len(payload.model_dump_json().encode())),
            ttl_s=ttl_s,
            vapid_claims={
                "aud": f"{parts.scheme}://{parts.netloc}",
                "exp": int(self._clock.now().timestamp()) + VAPID_EXP_S,
                "sub": self._subject,
            },
        )
