"""API key scopes (P0-14, FR-14.10). Spec skeleton."""

from typing import Final

SCOPES: Final = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "context:read",
        "knowledge:write",
        "drafts:write",
        "delegate",
        "ingest",
    }
)
