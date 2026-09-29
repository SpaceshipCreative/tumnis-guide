"""The api's DBOS client in tumnis.core.deadletter: a client the module built is destroyed
before anything replaces it (P0-07). One dropped without `destroy()` leaves its pooled
connections to the DBOS system database for the garbage collector, which reports them as
"deleted while still open" in whatever test happens to be running."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest


class FakeClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.destroyed = False

    def destroy(self) -> None:
        self.destroyed = True


@pytest.fixture
def deadletter(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import dbos  # noqa: PLC0415

    from tumnis.core import deadletter  # noqa: PLC0415

    monkeypatch.setattr(dbos, "DBOSClient", FakeClient)
    deadletter.configure("postgresql://unused/tumnis_dbos")
    try:
        yield deadletter
    finally:
        deadletter.configure(None)


@pytest.mark.req("REL-3")
@pytest.mark.wp("P0-07")
def test_issue_32_use_client_destroys_the_client_it_replaces(deadletter: Any) -> None:
    owned = deadletter._dbos_client()
    handed_in = FakeClient()

    deadletter.use_client(handed_in)

    assert owned.destroyed
    assert deadletter._dbos_client() is handed_in
    deadletter.close()
    assert not handed_in.destroyed  # its owner (the dbos_client fixture) destroys it
