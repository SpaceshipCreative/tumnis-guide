"""integrations pure rules: taint propagation (P0-12, FR-14.2, SAF-1 groundwork)."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-12")
@pytest.mark.parametrize(
    ("inputs", "tainted"),
    [
        ((), False),
        ((False,), False),
        ((False, False, False), False),
        ((True,), True),
        ((True, True), True),
        ((False, True), True),
        ((True, False, False), True),
    ],
)
def test_propagate_taint(inputs: tuple[bool, ...], tainted: bool) -> None:
    """T-P0-12-13
    Table test: any tainted input gives tainted output; trusted-only inputs (and no inputs)
    stay trusted.
    """
    from tumnis.modules.integrations.rules import propagate_taint  # noqa: PLC0415

    assert propagate_taint(*inputs) is tainted


@pytest.mark.req("FR-14.1")
@pytest.mark.wp("P0-12")
def test_record_owner_names_each_types_module() -> None:
    """Integrations owns people, threads, messages, notes and artifacts; calendar owns
    events and knowledge documents; an unknown type is refused."""
    from tumnis.modules.integrations.rules import record_owner  # noqa: PLC0415

    owners = {t: record_owner(t) for t in ("person", "thread", "message", "note", "artifact")}
    assert set(owners.values()) == {"integrations"}
    assert (record_owner("event"), record_owner("document")) == ("calendar", "knowledge")
    with pytest.raises(ValueError, match="unknown"):
        record_owner("invoice")
