"""The main worker's listen list (P1-16, ADR-0007): every queue register_queues registers,
except `extract`. DBOS.listen_queues takes names before launch while queues are registered
after it, so the two lists live apart; a queue added to one and not the other would never
be dequeued."""

from __future__ import annotations

from typing import Any

import pytest
from dbos import DBOS

from tumnis import worker


@pytest.mark.req("ADR-0007")
@pytest.mark.wp("P1-16")
def test_main_worker_listens_to_every_queue_but_extract(monkeypatch: pytest.MonkeyPatch) -> None:
    registered: list[str] = []

    def record(name: str, **_: Any) -> None:
        registered.append(name)

    monkeypatch.setattr(DBOS, "register_queue", record)

    worker.register_queues()

    assert worker.EXTRACT_QUEUE in registered
    assert sorted(worker.main_queues()) == sorted(
        q for q in registered if q != worker.EXTRACT_QUEUE
    )
