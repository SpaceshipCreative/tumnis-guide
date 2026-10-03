"""APP-F05 (final application test, was APP-10; Scott decision 98): `GET /v1/plan/{day}`
answered 404 before the planner published the day's plan, and the browser logged the
dashboard's read as a console error. A day without a published plan now answers 200 with
a null body. A published plan reads as before (T-P1-11-13), and a date that is not a
date is still refused as it was.

Day: Monday 2026-03-09 in New York (the `workspace` fixture), nothing built yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tumnis.modules.planning.tests.integration._plan import MONDAY

if TYPE_CHECKING:
    from tests._auth import SessionClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("FR-4.3", "FR-1.2"),
    pytest.mark.wp("P1-11"),
]


async def test_plan_read_before_publish_answers_200_null(session_client: SessionClient) -> None:
    """APP-F05: a day with no published plan answers 200 with `null`, not 404."""
    response = await session_client.get(f"/v1/plan/{MONDAY.isoformat()}")
    assert response.status_code == 200
    assert response.json() is None


async def test_plan_read_of_an_impossible_date_is_still_refused(
    session_client: SessionClient,
) -> None:
    """APP-F05: only "no plan yet" changed; a path that is not a date (30 February) is
    refused as before, with 422 `validation_error`."""
    response = await session_client.get("/v1/plan/2026-02-30")
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
