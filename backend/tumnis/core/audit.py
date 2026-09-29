"""Append-only audit log with a per-workspace SHA-256 hash chain (P0-15, SEC-3).

Stubs until the P0-15 implementation lands.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

GENESIS: Final = bytes(32)
SENSITIVE_KEY: Final = re.compile(
    r"(token|secret|password|passwd|hmac|key|authorization|cookie|body|prompt|content|text)",
    re.IGNORECASE,
)
MAX_DETAIL_STR: Final = 200  # plan default

BreakKind = Literal["hash_mismatch", "gap", "anchor_mismatch", "truncated_after_anchor"]


@dataclass(frozen=True)
class ChainBreak:
    workspace_id: UUID
    seq: int
    kind: BreakKind


def redact_details(details: Mapping[str, Any]) -> dict[str, Any]:
    raise NotImplementedError("P0-15")


async def record(
    session: AsyncSession,
    action: str,
    *,
    target: tuple[str, UUID] | None = None,
    reason: str | None = None,
    details: Mapping[str, Any] | None = None,
    occurred_at: datetime,
) -> None:
    raise NotImplementedError("P0-15")


async def verify_chain(session: AsyncSession, workspace_id: UUID) -> list[ChainBreak]:
    raise NotImplementedError("P0-15")


async def anchor(session: AsyncSession, workspace_id: UUID, now: datetime) -> None:
    raise NotImplementedError("P0-15")
