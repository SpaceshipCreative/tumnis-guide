"""API keys, task tokens and device tokens: generate, parse, hash, verify (P0-14, SEC-2,
ADR-0010). Spec skeleton: filled by the P0-14 implementation."""

import hmac
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal
from uuid import UUID

from tumnis.core.crypto import MasterKeys

Kind = Literal["tmn", "tmt", "tmd"]
KEY_RE: Final = re.compile(r"^(tmn|tmt|tmd)_([a-z2-7]{12})_([A-Za-z0-9_-]{43})$")


@dataclass(frozen=True)
class NewSecret:
    display: str  # "tmn_<prefix>_<secret>", returned once
    prefix: str
    secret_hmac: bytes
    pepper_version: int


@dataclass(frozen=True)
class ParsedSecret:
    kind: Kind
    prefix: str
    secret: str


@dataclass(frozen=True)
class KeyRow:
    key_id: UUID
    workspace_id: UUID
    secret_hmac: bytes
    pepper_version: int
    scopes: frozenset[str]
    project_ids: frozenset[UUID] | None
    expires_at: datetime | None
    revoked_at: datetime | None


def parse(display: str) -> ParsedSecret | None:
    raise NotImplementedError("P0-14")


def generate(kind: Kind, peppers: MasterKeys) -> NewSecret:
    raise NotImplementedError("P0-14")


def hmac_secret(pepper: bytes, secret: str) -> bytes:
    return hmac.new(pepper, secret.encode(), "sha256").digest()


def verify(candidate_secret: str, rows: Sequence[KeyRow], peppers: MasterKeys) -> KeyRow | None:
    raise NotImplementedError("P0-14")
