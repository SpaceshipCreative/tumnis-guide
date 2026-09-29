"""Schemathesis fuzzes every /v1 operation of the app with fakes and a test principal: no
5xx, and every response matches its declared schema and status codes (P0-11, SAAS-1).

Expected 4xx answers (404, 409, 422, ...) pass as long as the route declares them; every
`v1_router` route declares the problem responses (P0-10), and this suite checks them. The
test routes (`/v1/test/*`) are left out.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest
import schemathesis
from hypothesis import settings

if TYPE_CHECKING:
    from schemathesis import Case

pytestmark = [
    pytest.mark.contract,
    pytest.mark.integration,
    pytest.mark.enable_socket,
    pytest.mark.req("SAAS-1"),
    pytest.mark.wp("P0-11"),
]


@pytest.fixture
def api_schema(app_with_fakes: Any) -> Any:
    """The app's own /v1/openapi.json, loaded in-process."""
    return schemathesis.openapi.from_asgi("/v1/openapi.json", app_with_fakes.app)


schema = schemathesis.pytest.from_fixture("api_schema").exclude(path_regex="^/v1/test/")


@pytest.mark.xfail(strict=True, reason="spec:P0-11")
@schema.parametrize()
@settings(max_examples=25, deadline=None)  # plan default per operation
def test_api(case: Case, app_with_fakes: Any) -> None:
    """T-P0-11-09
    Every /v1 operation, fuzzed with fakes and a full-scope test principal: no 5xx, and
    responses match the declared schema and status codes.
    """
    case.call_and_validate(
        headers={
            "Idempotency-Key": uuid.uuid4().hex,
            **app_with_fakes.principal_headers,  # a real key header after P0-14
        }
    )
