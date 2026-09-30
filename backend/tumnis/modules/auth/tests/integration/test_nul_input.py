"""A NUL character in sign-in input is a validation error, never a 500 (bug: the P0-11
contract fuzzer's `POST /v1/auth/login` with `"\\u0000"` in the email). PostgreSQL text
cannot hold NUL, so psycopg raised DataError on the lookup and the api answered 500."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests._auth import password_step

if TYPE_CHECKING:
    import httpx

    from tests._auth import Account

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

NUL = "\x00"


@pytest.mark.req("SEC-1", "SAAS-1")
@pytest.mark.wp("P0-13")
async def test_login_with_nul_in_email_is_422_not_500(
    client: httpx.AsyncClient, account: Account
) -> None:
    """A NUL in the email is 422 `validation_error`, the answer other malformed sign-in
    input gets; the same for a known and an unknown account, so it tells no one which
    emails exist."""
    known = await password_step(client, account.email + NUL, account.password)
    unknown = await password_step(client, "nobody@example.com" + NUL, account.password)

    for response in (known, unknown):
        assert response.status_code == 422, response.text
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["code"] == "validation_error"
    assert known.json() == unknown.json()


@pytest.mark.req("SEC-1", "SAAS-1")
@pytest.mark.wp("P0-13")
async def test_login_with_nul_in_password_is_422_not_500(
    client: httpx.AsyncClient, account: Account
) -> None:
    """A NUL in the password (or any other credential field) is refused the same way, as
    malformed input (it was 401 `invalid_credentials` before), whatever the account."""
    response = await password_step(client, account.email, account.password + NUL)

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"
