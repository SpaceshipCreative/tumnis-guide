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
    """Script the Jev fake's `quick_add_label` answer (P1-01's fake; P1-07's decision)."""
    fakes["decisions.jev"].script("quick_add_label", answer=label, confidence=confidence)


def fail_decision_providers(fakes: Any) -> None:
    """Make both decision providers fail as unavailable: Jev and the vLLM fallback (P1-02)."""
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    for name in ("decisions.jev", "decisions.vllm"):
        fakes[name].script_failure(AdapterUnavailable(f"{name} down (A1.4)"))


# What `knowledge_app` changed in this process (the in-process pipeline's settings and
# parts); the acceptance conftest puts it back after each test.
KNOWLEDGE_APP_UNDO: list[Callable[[], object]] = []


def knowledge_app(app_factory: Any, minio: S3Endpoint, clamd: ClamdEndpoint) -> Any:
    """The app with the knowledge pipeline on real services: MinIO as the seed workspace's
    default storage location (P1-14), clamd for scanning and the real Docling converter
    (P1-16), every other adapter a fake.

    The api spools uploads to a temporary folder that the in-process pipeline (run by the
    `dbos` fixture) reads, with its own scratch folder. The seed has no storage location,
    so on the first request the returned app saves a MinIO bucket as the workspace default
    and gives every seed project its folder there."""
    import shutil  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.modules.knowledge import pipeline  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.clamav import ClamAV  # noqa: PLC0415
    from tumnis.modules.knowledge.adapters.docling import DoclingExtractor  # noqa: PLC0415
    from tumnis.settings import KnowledgeSettings  # noqa: PLC0415

    work = Path(tempfile.mkdtemp(prefix="a1-knowledge-"))
    (work / "spool").mkdir()
    (work / "scratch").mkdir()
    settings = KnowledgeSettings(
        spool_dir=str(work / "spool"),
        scratch_dir=str(work / "scratch"),
        clamd_host=clamd.host,
        clamd_port=clamd.port,
    )
    app = app_factory(knowledge=settings)
    previous_settings = pipeline.configure(settings)
    previous_parts = pipeline.use(
        scanner=ClamAV(clamd.host, clamd.port, clock=SystemClock()),
        extractor=DoclingExtractor(chunk_tokenizer=settings.chunk_tokenizer),
    )
    KNOWLEDGE_APP_UNDO.append(lambda: shutil.rmtree(work, ignore_errors=True))
    KNOWLEDGE_APP_UNDO.append(lambda: pipeline.use(**previous_parts))
    KNOWLEDGE_APP_UNDO.append(lambda: pipeline.configure(previous_settings))
    return _WithMinioLocation(app, minio)


class _WithMinioLocation:
    """An ASGI app that, before its first request, saves a MinIO bucket as the (single)
    seed workspace's default location and assigns each project its folder on it."""

    def __init__(self, app: Any, minio: S3Endpoint) -> None:
        self.app = app
        self.minio = minio
        self.ready = False
        self.lock = asyncio.Lock()

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and not self.ready:
            async with self.lock:
                if not self.ready:
                    await self._default_location()
                    self.ready = True
        await self.app(scope, receive, send)

    async def _default_location(self) -> None:
        from sqlalchemy import text  # noqa: PLC0415

        from tumnis.core import db as core_db  # noqa: PLC0415
        from tumnis.core.net import NetPolicy  # noqa: PLC0415
        from tumnis.core.tenancy import WorkspaceContext, tenant_session  # noqa: PLC0415
        from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415
        from tumnis.modules.knowledge import api as knowledge  # noqa: PLC0415
        from tumnis.modules.knowledge.tests.contract.test_storage_s3 import (  # noqa: PLC0415
            make_bucket,
        )
        from tumnis.modules.knowledge.tests.integration.test_locations import (  # noqa: PLC0415
            _lan_endpoint,
        )

        async with core_db.owner_sessionmaker()() as owner:
            (workspace_id,) = (await owner.execute(text("SELECT id FROM workspaces"))).one()
            projects: list[uuid.UUID] = list(
                (
                    await owner.execute(text("SELECT id FROM projects WHERE deleted_at IS NULL"))
                ).scalars()
            )
        net = NetPolicy(mode="self-hosted")
        location = knowledge.LocationIn(
            name="minio",
            kind="s3",
            root=await make_bucket(self.minio),
            is_default=True,
            s3=knowledge.S3ConfigIn(
                endpoint=_lan_endpoint(self.minio),
                region=self.minio.region,
                access_key=self.minio.access_key,
                secret_key=self.minio.secret_key,
            ),
        )
        async with tenant_session(WorkspaceContext(workspace_id, SYSTEM_ACTOR)) as s:
            await knowledge.create_location(s, location, net=net)
            for project in projects:
                await knowledge.ensure_project_folder(s, project, net=net)


async def upload(http: SessionClient, project: str, fixture: str) -> Json:
    """`POST /v1/knowledge/documents` (multipart) with a file from backend/fixtures/extraction
    (`eicar.txt` is made at run time: scanners on dev machines quarantine a committed copy)."""
    from tumnis.modules.knowledge.tests._samples import fixture_bytes  # noqa: PLC0415

    response = await http.post(
        "/v1/knowledge/documents",
        data={"project_id": project},
        files={"file": (fixture, fixture_bytes(fixture))},
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
