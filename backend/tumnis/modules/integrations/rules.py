"""integrations pure rules: no I/O, `now` and `tz` passed in.

Taint (SAF-1 groundwork, FR-14.2): outside content is untrusted, and whatever is made from
it carries the taint. Ownership: each canonical record type lives in one module's table.
"""

from typing import Final, Literal

RecordOwner = Literal["integrations", "calendar", "knowledge"]

RECORD_OWNERS: Final[dict[str, RecordOwner]] = {
    "person": "integrations",
    "thread": "integrations",
    "message": "integrations",
    "note": "integrations",
    "artifact": "integrations",
    "event": "calendar",
    "document": "knowledge",
}


def propagate_taint(*tainted: bool) -> bool:
    """Tainted when any input is (SAF-1): a context item, a proposal or a task made from
    outside content carries the taint of what it came from. No inputs: trusted."""
    return any(tainted)


def record_owner(record_type: str) -> RecordOwner:
    """The module that owns a canonical record type's table; ValueError for a type no
    module owns."""
    try:
        return RECORD_OWNERS[record_type]
    except KeyError:
        raise ValueError(f"unknown canonical record type {record_type!r}") from None
