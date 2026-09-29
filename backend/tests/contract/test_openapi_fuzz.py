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
    # Schemathesis 4.28 shuts the app's lifespan down after the test without closing the
    # lifespan's anyio memory streams, which then warn when collected.
    pytest.mark.filterwarnings("ignore:Unclosed <MemoryObject:ResourceWarning"),
]


# Data that matches the schema may still break a rule no schema describes, and the answer
# is a documented problem: an opaque cursor (400 `invalid_cursor`), a stale `version` (409),
# an IANA zone name or a range checked by the module (422). Negative data is not required
# to be refused: Pydantic's lax parsing accepts, for example, a unix time for a `date`
# query parameter; this suite proves no 5xx and conformance to the declared responses.
CONFIG = schemathesis.Config.from_dict(
    {
        "checks": {
            "positive_data_acceptance": {
                "expected-statuses": ["2xx", 400, 401, 403, 404, 409, 422],
            },
            "negative_data_rejection": {"enabled": False},
        },
    }
)


@pytest.fixture
def api_schema(app_with_fakes: Any) -> Any:
    """The app's own /v1/openapi.json, loaded in-process."""
    return schemathesis.openapi.from_asgi("/v1/openapi.json", app_with_fakes.app, config=CONFIG)


schema = (
    schemathesis.pytest.from_fixture("api_schema")
    .include(path_regex="^/v1/")
    .exclude(path_regex="^/v1/test/")
)


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
