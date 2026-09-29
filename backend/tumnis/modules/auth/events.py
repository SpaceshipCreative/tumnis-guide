"""auth event payload models and subscribers.

`auth.failed` (A8): a failed password or code on a known account, emitted in the
account's workspace in the same transaction as its audit row. Unknown emails have no
workspace to attribute them to and are only counted (auth_throttle) and logged.
"""

from typing import ClassVar, Literal
from uuid import UUID

from tumnis.core.events import EventPayload, event_type


@event_type("auth.failed", 1)
class AuthFailedV1(EventPayload):
    event_name: ClassVar[str] = "auth.failed"
    schema_version: Literal[1] = 1
    user_id: UUID
    source_ip: str | None
    step: Literal["password", "totp"]
    locked: bool = False
