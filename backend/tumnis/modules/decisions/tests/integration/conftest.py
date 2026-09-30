"""Fixtures for the decisions integration tests (P1-02): the core database configured on
the test clone, a process cache on the test clock, and the two decision fakes."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock


@pytest.fixture
async def core_db(db: DbUrls) -> AsyncIterator[None]:
    """tumnis.core.db on the test's database (app role, no pool)."""
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    try:
        yield
    finally:
        await core_db.dispose()


@pytest.fixture
def test_cache(clock: FixedClock) -> Iterator[Any]:
    """The process cache swapped for one reading the test clock, so TTLs follow it."""
    from tumnis.core.cache import InProcessCache, use_backend  # noqa: PLC0415

    with use_backend(InProcessCache(clock)) as cache:
        yield cache


@pytest.fixture
def providers(fakes: Fakes) -> Any:
    """The Jev fake as the primary and a second fake as the vLLM fallback."""
    from tumnis.modules.decisions.api import Providers  # noqa: PLC0415

    return Providers(jev=fakes["decisions.jev"], vllm=fakes["decisions.vllm"])


@pytest.fixture
def make_decision_log(workspace: WorkspaceHandle, clock: FixedClock) -> Any:
    """`await make_decision_log(n, accuracy)`: n synthetic `project_match` decisions in the
    workspace's log, as P3-08's calibration reads them. By position: 4 in 10 went to review
    and the human decided them (accepted when the answer was right, else edited to `p02`);
    5 in 10 were applied 10 days ago (left alone when right, else overridden to `p02`); 1
    in 10 was applied yesterday and has not settled. Every 15th is a vLLM fallback answer.
    About `accuracy` of the answers are right, spread over the rows."""
    import hashlib  # noqa: PLC0415
    import uuid  # noqa: PLC0415
    from datetime import timedelta  # noqa: PLC0415

    from sqlalchemy import insert  # noqa: PLC0415

    from tumnis.core.tenancy import tenant_session  # noqa: PLC0415
    from tumnis.modules.decisions.models import DecisionLog  # noqa: PLC0415

    def row(i: int, correct: bool) -> dict[str, Any]:
        now = clock.now()
        fallback = i % 15 == 0
        human: tuple[bool, str | None] | None
        if i % 10 < 4:  # review, decided by the human an hour later
            route, created = "review", now - timedelta(days=2)
            confidence = 0.6 + (i % 5) * 0.05
            human = (False, None) if correct else (True, "p02")
        elif i % 10 < 9:  # applied long enough ago to have settled
            route, created = "applied", now - timedelta(days=10)
            confidence = 0.86 + (i % 3) * 0.05
            human = None if correct else (True, "p02")
        else:  # applied yesterday: not settled
            route, created = "applied", now - timedelta(days=1)
            confidence, human = 0.9, None
        return {
            "decision_point": "project_match",
            "subject_type": "message",
            "subject_id": uuid.uuid4(),
            "provider": "vllm" if fallback else "jev",
            "fallback": fallback,
            "fallback_reason": "primary_failed" if fallback else None,
            "model_version": "vllm-local" if fallback else "jev-1.13.0",
            "input_hash": hashlib.sha256(f"log-{i}".encode()).digest(),
            "fields_sent": ["message_subject"],
            "answer": {
                "project": {
                    "type": "choice",
                    "choice": "p01",
                    "probabilities": {"p01": confidence, "unknown": 1 - confidence},
                    "confidence": confidence,
                }
            },
            "confidence": confidence,
            "threshold": {"min_confidence": 0.85, "fallback_margin": 0.1},
            "outcome": route,
            "created_at": created,
            "overridden": None if human is None else human[0],
            "final_value": None if human is None else human[1],
            "outcome_at": None if human is None else created + timedelta(hours=1),
        }

    async def make(n: int, accuracy: float) -> None:
        rows = [row(i, (i * 37) % 100 < accuracy * 100) for i in range(n)]
        async with tenant_session(workspace.ctx) as s:
            await s.execute(insert(DecisionLog), rows)

    return make
