"""integrations pure rules: no I/O, `now` and `tz` passed in."""

from typing import Literal

RecordOwner = Literal["integrations", "calendar", "knowledge"]


def propagate_taint(*tainted: bool) -> bool:
    """Tainted when any input is (SAF-1): a context item, a proposal or a task made from
    outside content carries the taint of what it came from."""
    raise NotImplementedError


def record_owner(record_type: str) -> RecordOwner:
    """The module that owns a canonical record type's table."""
    raise NotImplementedError
