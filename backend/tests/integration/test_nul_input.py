"""A NUL character anywhere in a request's strings is 422 `validation_error` on every /v1
route, never a 500 (bug: the P0-11 contract fuzzer). PostgreSQL text cannot hold NUL, so
before the shared check in `TumnisRoute` each route that passed the string on to the
database answered 500 (psycopg DataError): sign-in (the auth module's own test), a JSON
body field (a project's name or client) and a query parameter (the audit log's `action`
filter) alike."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from tests._auth import SessionClient

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

NUL = "\x00"


@pytest.mark.req("SAAS-1")
@pytest.mark.wp("P0-10")
@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        pytest.param("POST", "/v1/projects", {"json": {"name": f"Acme{NUL}"}}, id="body-field"),
        pytest.param(
            "POST",
            "/v1/projects",
            {"json": {"name": "Acme", "client": f"{NUL}Acme"}},
            id="optional-body-field",
        ),
        pytest.param(
            "GET", "/v1/audit", {"params": {"action": f"auth.login{NUL}"}}, id="query-param"
        ),
    ],
)
async def test_nul_in_request_strings_is_422_not_500(
    session_client: SessionClient, method: str, path: str, kwargs: dict[str, Any]
) -> None:
    response = await session_client.request(method, path, **kwargs)

    assert response.status_code == 422, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["code"] == "validation_error"
