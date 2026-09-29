"""integrations pure rules: taint propagation (P0-12, FR-14.2, SAF-1 groundwork)."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-14.2")
@pytest.mark.wp("P0-12")
@pytest.mark.xfail(strict=True, reason="spec:P0-12")
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
