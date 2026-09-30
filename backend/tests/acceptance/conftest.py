"""The acceptance suites' own fixtures.

- `seed`: the seed set, loaded only once the master key and the pepper are in place (the
  seed user's password and TOTP secret are stored with them), whatever order a test names
  its fixtures in, so the seed user can sign in to the `app` fixture's app.
- No test leaves decision fakes behind for the next (`_phase1.script_label` and
  `fail_decision_providers` set them process-wide, P1-07).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

from tests._labels import reset_label_fakes

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, PepperFile
    from tumnis.core.clock import FixedClock
    from tumnis.seed import SeedResult


@pytest.fixture
async def seed(
    db: DbUrls, clock: FixedClock, master_key_file: MasterKeyFile, pepper_file: PepperFile
) -> SeedResult:
    from tests.fixtures import SEED_SET, _load_set  # noqa: PLC0415

    del master_key_file, pepper_file  # requested for their order only
    return await _load_set(SEED_SET, db, clock)


@pytest.fixture(autouse=True)
def _reset_label_fakes() -> Iterator[None]:
    yield
    reset_label_fakes()
