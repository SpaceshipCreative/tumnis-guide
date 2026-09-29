"""The named seed sets `POST /v1/test/reset?set=` and `tumnis seed --set` load (P0-23 adds
`ten_projects`: the seed plus seven projects, for the dashboard's no-scroll check at
1280 x 800, T-P0-23-10)."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tumnis.core.clock import FixedClock

ANCHOR = date(2026, 3, 9)


@pytest.mark.req("UX 1")
@pytest.mark.wp("P0-23")
async def test_ten_projects_set_is_the_seed_plus_seven_projects(clock: FixedClock) -> None:
    """`ten_projects` loads the seed set unchanged, then seven projects sorted after it."""
    from tumnis.seed import SEED_PATHS, InMemorySink, SeedSet, load_seed  # noqa: PLC0415

    seed, ten = InMemorySink(), InMemorySink()
    await load_seed(SEED_PATHS[SeedSet.seed], seed, anchor=ANCHOR, clock=clock)
    await load_seed(SEED_PATHS[SeedSet("ten_projects")], ten, anchor=ANCHOR, clock=clock)

    def keys(sink: InMemorySink, kind: str) -> list[str]:
        return [stored.rec.key for stored in sink.records if stored.kind == kind]

    assert keys(ten, "workspace") == keys(seed, "workspace")
    assert keys(ten, "user") == keys(seed, "user")
    seed_projects, ten_projects = keys(seed, "project"), keys(ten, "project")
    assert len(ten_projects) == 10
    assert ten_projects[:3] == seed_projects
    assert keys(ten, "task")[: len(keys(seed, "task"))] == keys(seed, "task")
    # Distinct board keys after the seed's, so the extra projects sort last.
    sort_keys = [
        stored.rec.sort_key  # type: ignore[attr-defined]
        for stored in ten.records
        if stored.kind == "project"
    ]
    assert len(set(sort_keys)) == 10
    assert sort_keys == sorted(sort_keys)
