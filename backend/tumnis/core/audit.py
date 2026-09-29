"""Append-only audit log with a per-workspace SHA-256 hash chain (P0-15, SEC-3).

Stubs until the P0-15 implementation lands.
"""

import math
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
# A Tumnis credential (API key, task token, device token) wherever it sits in a string.
CREDENTIAL: Final = re.compile(r"tm[ntd]_[A-Za-z0-9_\-]")
REDACTED: Final = "[redacted]"

BreakKind = Literal["hash_mismatch", "gap", "anchor_mismatch", "truncated_after_anchor"]


@dataclass(frozen=True)
class ChainBreak:
    workspace_id: UUID
    seq: int
    kind: BreakKind


def redact_details(details: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively drops values under sensitive keys (replaced by "[redacted]"), truncates
    long strings, and drops any string that looks like a Tumnis credential (tmn_, tmt_,
    tmd_ prefixes) wherever it sits. Values that are not JSON types become strings first,
    so the result is plain JSON (what jsonb stores and the hash chain covers)."""
    return {str(key): _redact_value(str(key), value) for key, value in details.items()}


def _redact_value(key: str, value: Any) -> Any:
    if SENSITIVE_KEY.search(key):
        return REDACTED
    return _clean(value)


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return redact_details(value)
    if isinstance(value, list | tuple | set | frozenset):
        return [_clean(item) for item in value]
    text = value if isinstance(value, str) else str(value)
    if CREDENTIAL.search(text):
        return REDACTED
    return text if len(text) <= MAX_DETAIL_STR else text[:MAX_DETAIL_STR] + "…"


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
