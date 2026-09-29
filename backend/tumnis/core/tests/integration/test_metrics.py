"""/metrics: bearer token, the required families, queue depth from the DBOS system tables
(P0-27, FR-12.3, REL-5)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from dbos import DBOS, DBOSClient
    from prometheus_client.metrics_core import Metric

    from tests._pg import DbUrls
    from tests.fixtures import Fakes, MasterKeyFile
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

TOKEN = "metrics-scrape-token"
REQUIRED_FAMILIES = (
    "tumnis_http_request_duration_seconds",
    "tumnis_queue_depth",
    "tumnis_workflows",
    "tumnis_dead_letters",
    "tumnis_cache_hits",  # the parser names counter families without `_total`
    "tumnis_cache_misses",
    "tumnis_usage_total",
    "tumnis_ops_check_ok",
    "tumnis_ops_check_timestamp_seconds",
)


@pytest.fixture
async def metrics_client(
    db: DbUrls,
    dbos_sys_db: DbUrls,
    *,
    clock: FixedClock,
    fakes: Fakes,
    master_key_file: MasterKeyFile,
    tmp_path: Path,
) -> AsyncIterator[httpx.AsyncClient]:
    """The app with METRICS_TOKEN_FILE holding TOKEN, and an httpx client on it."""
    import httpx  # noqa: PLC0415

    from tests.fixtures import settings_for  # noqa: PLC0415
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    token_file = tmp_path / "metrics_token"
    token_file.write_text(TOKEN + "\n")
    settings = settings_for(db, dbos_sys_db, metrics_token_file=str(token_file))
    app = create_app(settings=settings, clock=clock)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as http:
            yield http
    finally:
        await core_db.dispose()


async def _scrape(client: httpx.AsyncClient) -> dict[str, Metric]:
    from prometheus_client.parser import text_string_to_metric_families  # noqa: PLC0415

    response = await client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200, response.text
    return {family.name: family for family in text_string_to_metric_families(response.text)}


@pytest.mark.req("FR-12.3")
@pytest.mark.wp("P0-27")
async def test_metrics_requires_bearer_token(metrics_client: httpx.AsyncClient) -> None:
    """T-P0-27-03
    No Authorization header: 401. A wrong bearer token: 401. The token from
    METRICS_TOKEN_FILE: 200 with the Prometheus text format.
    """
    assert (await metrics_client.get("/metrics")).status_code == 401
    wrong = await metrics_client.get("/metrics", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401
    right = await metrics_client.get("/metrics", headers={"Authorization": f"Bearer {TOKEN}"})
    assert right.status_code == 200
    assert right.headers["content-type"].startswith("text/plain")


@pytest.mark.req("FR-12.3")
@pytest.mark.wp("P0-27")
async def test_metrics_expose_required_families(
    metrics_client: httpx.AsyncClient, db: DbUrls
) -> None:
    """T-P0-27-04
    The parsed exposition has the request histogram, queue depth, workflows by status, dead
    letters, cache hits and misses, usage and the ops checks (audit chain and backups).
    A request to a route with a path parameter is labelled with the route template, never
    the raw ID.
    """
    raw_id = uuid.uuid4()
    await metrics_client.post(f"/v1/dead-letters/{raw_id}/retry", json={"version": 1})
    families = await _scrape(metrics_client)

    missing = [name for name in REQUIRED_FAMILIES if name not in families]
    assert not missing, f"families missing from /metrics: {missing}"
    routes = {
        sample.labels["route"]
        for sample in families["tumnis_http_request_duration_seconds"].samples
        if "route" in sample.labels
    }
    assert "/v1/dead-letters/{dead_letter_id}/retry" in routes
    assert not any(str(raw_id) in route for route in routes)
    statuses = {s.labels["status"] for s in families["tumnis_dead_letters"].samples}
    assert {"open", "retrying", "resolved", "discarded"} <= statuses


@pytest.mark.req("FR-12.3", "REL-5")
@pytest.mark.wp("P0-27")
async def test_queue_depth_counts_enqueued_workflows(
    metrics_client: httpx.AsyncClient, dbos: type[DBOS], dbos_client: DBOSClient
) -> None:
    """T-P0-27-05
    Three workflows enqueued on a queue no executor listens to show as
    tumnis_queue_depth{queue="t_idle"} 3; the registered `events` queue reports 0, and
    tumnis_workflows{status="ENQUEUED"} counts the three.
    """
    for i in range(3):
        dbos_client.enqueue(
            {"queue_name": "t_idle", "workflow_name": "deliver_event", "workflow_id": f"idle-{i}"},
            "testa.record",
            {},
        )
    families = await _scrape(metrics_client)
    depth = {s.labels["queue"]: s.value for s in families["tumnis_queue_depth"].samples}
    assert depth["t_idle"] == 3
    assert depth["events"] == 0
    workflows = {s.labels["status"]: s.value for s in families["tumnis_workflows"].samples}
    assert workflows["ENQUEUED"] == 3
