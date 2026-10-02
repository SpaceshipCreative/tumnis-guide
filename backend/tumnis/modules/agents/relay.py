"""Replies the master relays from its chat channel (P2-16, FR-8.2).

The master's `record_human_reply` (agents.mcp) records a person's answer typed in the
master's one chat channel exactly as the app records it. A question (agents' own review
kind) is answered here through the review queue; every other kind of item a reply may
answer is owned by another module, which registers how to record it with
`register_reply_handler(kind, handler)` from its `mcp.py` (focus registers `focus`). The
handler gets the person's context and the caller's transaction:
`await handler(ctx, item_id, answer, now=now, session=session)`.

Approvals and results are never answered from the chat (`NEEDS_APP`): an agent key must
never approve a gated action or accept its own result (Part B deviation 7).
"""

from collections.abc import Awaitable
from datetime import datetime
from typing import Final, Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core.tenancy import WorkspaceContext

# Item kinds a chat reply may never decide: the person decides them in the app.
NEEDS_APP: Final[frozenset[str]] = frozenset({"approval", "result"})


class ReplyHandler(Protocol):
    def __call__(
        self,
        ctx: WorkspaceContext,
        item_id: UUID,
        answer: str,
        /,
        *,
        now: datetime,
        session: AsyncSession,
    ) -> Awaitable[None]: ...


_handlers: dict[str, ReplyHandler] = {}


def register_reply_handler(kind: str, handler: ReplyHandler) -> None:
    """How a module records a relayed reply to its `kind` of item; registering the same
    kind again replaces the handler (a module re-imported in tests)."""
    if kind in NEEDS_APP:
        raise ValueError(f"{kind} is decided in the app only")
    _handlers[kind] = handler


def reply_handler(kind: str) -> ReplyHandler | None:
    return _handlers.get(kind)
