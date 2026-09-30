"""Fixtures for the acceptance suites. No assertions live here."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _master_key_before_seed(request: pytest.FixtureRequest) -> None:
    """Load the master key before `seed` runs: the seed stores its user (who signs in
    through `_phase1.seed_client`) only when a key can seal the TOTP secret, and a test
    that asks for `seed` before `app_factory` would otherwise get no user."""
    if "seed" in request.fixturenames:
        request.getfixturevalue("master_key_file")


@pytest.fixture(autouse=True)
def _undo_knowledge_app() -> Iterator[None]:
    """Put back the in-process extraction pipeline that `_phase1.knowledge_app` pointed at
    clamd and Docling, so the next test on this worker starts from the defaults."""
    from tests.acceptance._phase1 import KNOWLEDGE_APP_UNDO  # noqa: PLC0415

    yield
    while KNOWLEDGE_APP_UNDO:
        KNOWLEDGE_APP_UNDO.pop()()
