"""auth event payload models and subscribers.

`auth.failed` (A8): a failed password or code on a known account, emitted in the
account's workspace in the same transaction as its audit row. Unknown emails have no
workspace to attribute them to and are only counted (auth_throttle) and logged.

`key.created` and `key.revoked` (P0-14, A8): an API key made or revoked, emitted in the
key's workspace with its audit row. Payloads carry the key id and its prefix (shown in
every key list), never the secret.
"""

from datetime import datetime
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


@event_type("key.created", 1)
class KeyCreatedV1(EventPayload):
    event_name: ClassVar[str] = "key.created"
    schema_version: Literal[1] = 1
    key_id: UUID
    prefix: str
    scopes: list[str]
    project_ids: list[UUID] | None = None
    expires_at: datetime | None = None
    source_ip: str | None = None


@event_type("key.revoked", 1)
class KeyRevokedV1(EventPayload):
    event_name: ClassVar[str] = "key.revoked"
    schema_version: Literal[1] = 1
    key_id: UUID
    prefix: str
    source_ip: str | None = None
