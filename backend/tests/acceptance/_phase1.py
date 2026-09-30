"""Shared helpers for the phase 1 acceptance suite (A1.4, A1.5), committed red with it.

Helpers hold no assertions: they sign in, wait, read rows and wire fakes, so the work
packages that build the pieces may adjust them without touching a locked test body
(spec-guard locks the tests, not this file). Everything under `tumnis.modules` is imported
inside the functions: most of it lands with phase 1, and collection must not depend on it.

The names follow the plan's phase 1 interfaces: the `fake_runner` fixture and its
`.offline(profile)` (P1-04), the `planner_tick` workflow and `GET /v1/plan/{day}` (P1-11),
`POST /v1/knowledge/documents` (P1-16), `GET /v1/knowledge/search` and
`GET /v1/tasks/{id}/packet` (P1-17). Seed names (`Acme site` with the project agent
`acme-site`, `Beta app` with `beta-app`, the master `tumnis-master`) are the ones the plan's
phase 1 seed additions give (P1-04, P1-06).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests._services import ClamdEndpoint, S3Endpoint
    from tumnis.core.clock import FixedClock
    from tumnis.core.tenancy import WorkspaceContext
    from tumnis.seed import SeedResult

# Monday 2026-03-09, the first weekday after the US DST change: 08:30 in New York.
MONDAY = date(2026, 3, 9)
MONDAY_PLAN_TIME = datetime(2026, 3, 9, 12, 30, tzinfo=UTC)

ACME, BETA = "Acme site", "Beta app"
ACME_AGENT, BETA_AGENT, MASTER = "acme-site", "beta-app", "tumnis-master"

BACKEND = Path(__file__).resolve().parents[2]
EXTRACTION = BACKEND / "fixtures" / "extraction"
# Scripted runner results (P1-04's fake runner; P1-08 and P1-11 add the scripts).
RUNNER_RECORDINGS = BACKEND / "tests" / "fakes" / "recordings" / "runner"

Json = dict[str, Any]


def seed_ctx(seed: SeedResult) -> WorkspaceContext:
    """The seed workspace as the system actor, for api reads inside `tenant_session`."""
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    return WorkspaceContext(seed.ids["ws_main"], SYSTEM_ACTOR)


async def seed_client(app: Any, clock: FixedClock) -> SessionClient:
    """An httpx client on `app` signed in as the seed user (password, then TOTP at the
    clock's time); writes carry `X-CSRF-Token` and an `Idempotency-Key`."""
    from tests._auth import Account, session_client_for, sign_in  # noqa: PLC0415
    from tests._auth import seed_user as seed_user_fields  # noqa: PLC0415

    email, password, secret = seed_user_fields()
    http = session_client_for(app)
    unused = uuid.UUID(int=0)  # sign_in sends only the email, password and code
    await sign_in(http, Account(email, password, secret, unused, unused), clock)
    return http


async def settle[T](
    check: Callable[[], Awaitable[T | None]], *, timeout_s: float = 60, poll_s: float = 0.2
) -> T | None:
    """Relay the outbox (so `task.created` and friends reach their subscribers) and poll
    `check` until it returns something truthy or `timeout_s` passes; returns the last value.
    The in-process `dbos` fixture runs the queued workflows."""
    from tumnis.core.events import relay_once  # noqa: PLC0415

    deadline = time.monotonic() + timeout_s
    while True:
        await relay_once()
        value = await check()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(poll_s)


def rows(db: DbUrls, query: str, *params: Any) -> list[Json]:
    """Rows of a read as the owner (no row-level security), as dicts."""
    import psycopg  # noqa: PLC0415
    from psycopg.rows import dict_row  # noqa: PLC0415

    from tests._pg import OWNER  # noqa: PLC0415

    with psycopg.connect(db.libpq(OWNER), row_factory=dict_row) as conn:
        return list(conn.execute(query, params).fetchall())


async def get_json(http: SessionClient, path: str, **params: Any) -> Any:
    """GET `path`; raises on an error status."""
    response = await http.get(path, params=params)
    response.raise_for_status()
    return response.json()


async def project_id(http: SessionClient, name: str) -> str:
    """The id of the one project with this exact name."""
    body = await get_json(http, "/v1/projects", limit=100)
    items = body["items"] if isinstance(body, dict) else body
    hits = [item["id"] for item in items if item["name"] == name]
    if len(hits) != 1:
        raise LookupError(f"{len(hits)} projects named {name!r}")
    return str(hits[0])


async def create_task(http: SessionClient, project: str, title: str, **fields: Any) -> Json:
    """`POST /v1/tasks` as the seed user (the quick-add path); raises on an error status."""
    response = await http.post("/v1/tasks", json={"project_id": project, "title": title, **fields})
    response.raise_for_status()
    body: Json = response.json()
    return body


async def get_task(http: SessionClient, task_id: str) -> Json:
    body: Json = await get_json(http, f"/v1/tasks/{task_id}")
    return body


async def published_plan(http: SessionClient, day: date) -> Json | None:
    """`GET /v1/plan/{day}` once a plan is published for the day, else None."""
    response = await http.get(f"/v1/plan/{day.isoformat()}")
    if response.status_code == 404:
        return None
    response.raise_for_status()
    body: Json = response.json()
    return body if body.get("status") == "published" else None


async def day_calendar(http: SessionClient, day: date) -> Json:
    """`GET /v1/plan/{day}/calendar` (P1-10): window, events and free blocks."""
    body: Json = await get_json(http, f"/v1/plan/{day.isoformat()}/calendar")
    return body


def inside_a_free_block(block: Json, free_blocks: list[Json]) -> bool:
    """Whether `block` ({start, end}) lies inside one of `free_blocks`."""
    start, end = _instant(block["start"]), _instant(block["end"])
    return any(
        _instant(free["start"]) <= start and end <= _instant(free["end"]) for free in free_blocks
    )


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value)


async def run_planner_tick(clock: FixedClock) -> None:
    """One `planner-tick` at the clock's time, as the schedule would run it (P1-11); the
    `build_plan` it enqueues runs on the in-process `dbos` fixture's `maintenance` queue."""
    from dbos import DBOS  # noqa: PLC0415

    from tumnis.modules.planning.workflows import planner_tick  # noqa: PLC0415

    handle = await DBOS.start_workflow_async(planner_tick, clock.now(), None)
    await handle.get_result()


def runner_result(name: str) -> Json:
    """A scripted runner result from RUNNER_RECORDINGS (`<name>.result.json`)."""
    body: Json = json.loads((RUNNER_RECORDINGS / f"{name}.result.json").read_text())
    return body


def script_run(fake_runner: Any, profile: str, skill: str, output: Json, delay_ms: int = 0) -> None:
    """Script the fake runner's answer to `run` for (profile, skill) (P1-04's
    `{(profile, skill): (output_json, delay_ms, status)}` script; adjust to its final call)."""
    fake_runner.script(profile, skill, output, delay_ms=delay_ms, status="succeeded")


def script_label(fakes: Any, label: str, confidence: float) -> None:
    """Script the Jev fake's `quick_add_label` answer (P1-01's fake; P1-07's decision): the
    recorded answers with `label` winning at `confidence`, asked by every `decide` in this
    process (the queued workflows included) until the test's `reset_label_fakes`."""
    from tests._labels import label_answers, use_label_fakes  # noqa: PLC0415

    jev = fakes["decisions.jev"]
    jev.script("quick_add_label", label_answers(label, confidence))
    use_label_fakes(jev, fakes["decisions.vllm"])


def fail_decision_providers(fakes: Any) -> None:
    """Make both decision providers fail as unavailable for the quick-add label: Jev and the
    vLLM fallback (P1-02), asked by every `decide` in this process until the test's
    `reset_label_fakes`."""
    from tests._labels import use_label_fakes  # noqa: PLC0415
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    for name in ("decisions.jev", "decisions.vllm"):
        fakes[name].script("quick_add_label", fail=AdapterUnavailable(name, "ask", "down (A1.4)"))
    use_label_fakes(fakes["decisions.jev"], fakes["decisions.vllm"])


def knowledge_app(app_factory: Any, minio: S3Endpoint, clamd: ClamdEndpoint) -> Any:
    """The app with the knowledge pipeline on real services: MinIO as the seed workspace's
    default storage location (P1-14), clamd for scanning and the real Docling converter
    (P1-16), every other adapter a fake. The plan names the pieces but not the settings that
    select them, so P1-14 and P1-16 wire this seam."""
    raise NotImplementedError(
        f"P1-14/P1-16: build the app with storage on {minio.url} and clamd on "
        f"{clamd.host}:{clamd.port} (factory {app_factory!r})"
    )


async def upload(http: SessionClient, project: str, fixture: str) -> Json:
    """`POST /v1/knowledge/documents` (multipart) with a file from backend/fixtures/extraction."""
    path = EXTRACTION / fixture
    response = await http.post(
        "/v1/knowledge/documents",
        data={"project_id": project},
        files={"file": (path.name, path.read_bytes())},
    )
    response.raise_for_status()
    body: Json = response.json()
    return body


async def document(http: SessionClient, document_id: str) -> Json:
    body: Json = await get_json(http, f"/v1/knowledge/documents/{document_id}")
    return body


async def until_extracted(http: SessionClient, document_id: str) -> Json:
    """Poll the document until `extract_document` leaves it ready, quarantined or failed."""

    async def finished() -> Json | None:
        doc = await document(http, document_id)
        return doc if doc["status"] in {"ready", "quarantined", "failed"} else None

    return await settle(finished, timeout_s=300) or await document(http, document_id)


def chunks_of(db: DbUrls, document_id: str) -> list[Json]:
    """The chunks of a document's current version (P1-16's `chunks` table), in order."""
    return rows(
        db,
        "SELECT c.id::text AS id, c.page_from, c.page_to, c.heading_path, c.text "
        "FROM chunks c JOIN documents d ON d.current_version_id = c.document_version_id "
        "WHERE d.id = %s AND c.deleted_at IS NULL ORDER BY c.ordinal",
        document_id,
    )
