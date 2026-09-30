"""The quick-add label's subscribers are direct (P1-07, FR-3.3): the relay starts the
label as it reads the outbox, so the label never waits behind the events queue."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-3.3")
@pytest.mark.wp("P1-07")
@pytest.mark.parametrize("name", ["decisions.label_on_create", "decisions.label_on_title_change"])
def test_label_subscribers_are_direct(name: str) -> None:
    import tumnis.modules.decisions.events  # noqa: F401, PLC0415  # registers the subscribers
    from tumnis.core.events import get_subscriber  # noqa: PLC0415

    assert get_subscriber(name).direct is True
