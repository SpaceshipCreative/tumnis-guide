"""Command-line entry point: `tumnis api|worker|migrate|seed|gen|drill|audit` (more later).

The image runs every process through this CLI. `api` and `worker` load the deployment
settings and run the boot checks first; a configuration error exits 78 (EX_CONFIG), so a
misconfigured preview never serves a request.
"""

import asyncio
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, NoReturn
from uuid import UUID

import typer
from pydantic import ValidationError

from tumnis.core.clock import Clock, SystemClock
from tumnis.seed import SEED_PATHS, SeedSet
from tumnis.settings import EXIT_CONFIG, Settings, SettingsError

app = typer.Typer(name="tumnis", help="Tumnis Guide backend.", no_args_is_help=True)
drill_app = typer.Typer(help="Restore drill results (P0-28).", no_args_is_help=True)
app.add_typer(drill_app, name="drill")
keys_app = typer.Typer(help="Master key maintenance (P0-08, SEC-6).", no_args_is_help=True)
app.add_typer(keys_app, name="keys")
audit_app = typer.Typer(help="Audit log (P0-15).", no_args_is_help=True)
app.add_typer(audit_app, name="audit")

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def make_clock() -> Clock:
    """The CLI's clock; tests replace it with a FixedClock."""
    return SystemClock()


@app.callback()
def main() -> None:
    """Tumnis Guide backend."""


def _config_error(message: object) -> NoReturn:
    typer.echo(f"tumnis: {message}", err=True)
    raise typer.Exit(EXIT_CONFIG)


def load_settings() -> Settings:
    """Deployment settings from the environment; any configuration error exits 78."""
    try:
        return Settings()  # values come from the environment
    except SettingsError as exc:
        _config_error(exc)
    except ValidationError as exc:
        _config_error(f"invalid_settings: {exc}")


def check_metrics_token(settings: Settings) -> None:
    """The api refuses to start in prod without a readable METRICS_TOKEN_FILE (P0-27)."""
    try:
        settings.metrics_token()
    except SettingsError as exc:
        _config_error(exc)


def check_database_tls(settings: Settings) -> None:
    """Prod reaches the database only with sslmode=verify-full (P0-16, SEC-9)."""
    try:
        settings.check_database_tls()
    except SettingsError as exc:
        _config_error(exc)


def start_observability(settings: Settings, service: Literal["api", "worker"]) -> None:
    """JSON logs on stdout, the tracer provider (OTLP when OTEL_EXPORTER_OTLP_ENDPOINT is
    set) and GlitchTip (when SENTRY_DSN is set), before anything else logs (P0-27)."""
    from tumnis.core import errors_sentry, telemetry  # noqa: PLC0415
    from tumnis.core.logging import configure_logging  # noqa: PLC0415

    configure_logging()
    telemetry.setup_tracing(service)
    errors_sentry.init_sentry(
        settings.sentry_dsn,
        environment=settings.deployment_env,
        release=os.environ.get("TUMNIS_VERSION"),
    )


def run_boot_checks(settings: Settings) -> None:
    """The module kill list, the database checks, then the master key file (P0-08): each
    configuration error exits 78 before the api or the worker starts."""
    from tumnis.core.crypto import MasterKeyError  # noqa: PLC0415
    from tumnis.core.modules import deployment_disabled  # noqa: PLC0415
    from tumnis.settings import boot_checks, install_master_keys  # noqa: PLC0415

    try:
        deployment_disabled(settings)  # an unknown or required module in the kill list
        asyncio.run(boot_checks(settings))
        install_master_keys(settings)
    except (SettingsError, MasterKeyError) as exc:
        _config_error(exc)


@app.command()
def api(
    host: Annotated[str, typer.Option(help="Interface to bind")] = "0.0.0.0",  # noqa: S104  # inside the container only; no host port is published (FR-9.1)
    port: Annotated[int, typer.Option(help="Port to listen on")] = 8080,
) -> None:
    """Serve the HTTP API (and the built frontend) with uvicorn."""
    settings = load_settings()
    check_metrics_token(settings)
    check_database_tls(settings)
    run_boot_checks(settings)
    start_observability(settings, "api")
    import uvicorn  # noqa: PLC0415

    uvicorn.run(
        "tumnis.app:create_app",
        factory=True,
        host=host,
        port=port,
        proxy_headers=True,
        log_config=None,  # uvicorn's loggers propagate to the JSON handler (P0-27)
    )


@app.command()
def worker() -> None:
    """Launch DBOS: queues, workflows and schedules."""
    settings = load_settings()
    check_database_tls(settings)
    run_boot_checks(settings)
    start_observability(settings, "worker")
    from tumnis.worker import main as worker_main  # noqa: PLC0415

    worker_main(settings)


@app.command()
def migrate(
    check: Annotated[
        bool, typer.Option("--check", help="Exit 1 unless the database is at every head")
    ] = False,
) -> None:
    """Upgrade the database to every Alembic head as the owner role (DATABASE_OWNER_URL).
    A database ahead of this release (a rollback, REL-4) is left alone and exits 0."""
    from tumnis.migrate import (  # noqa: PLC0415
        DbPosition,
        MigrationPendingError,
        upgrade,
        verify_at_heads,
    )

    settings = load_settings()
    check_database_tls(settings)
    if settings.database_owner_url is None:
        _config_error("database_owner_url_missing: migrate runs as the owner role")
    try:
        if check:
            verify_at_heads(ALEMBIC_INI, settings.database_owner_url)
            return
        position = upgrade(ALEMBIC_INI, settings)
    except SettingsError as exc:
        _config_error(exc)
    except MigrationPendingError as exc:
        typer.echo(f"tumnis: {exc}", err=True)
        raise typer.Exit(1) from exc
    messages = {
        DbPosition.BEHIND: "upgraded to this release's heads",
        DbPosition.AT_HEAD: "database already at this release's heads",
        DbPosition.AHEAD: "database is ahead of this release (rollback); nothing to do",
    }
    typer.echo(f"migrate: {messages[position]}", err=True)


@app.command()
def seed(
    set_name: Annotated[
        SeedSet, typer.Option("--set", help="seed: 3 projects, 30 tasks; load: 2,000 tasks")
    ] = SeedSet.seed,
    anchor: Annotated[
        datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Day the offsets count from (default today)"),
    ] = None,
) -> None:
    """Load a seed set into the database at DATABASE_URL through each module's api."""
    import tumnis.wiring  # noqa: F401, PLC0415  # modules register their seed writers
    from tumnis.core import db  # noqa: PLC0415
    from tumnis.seed import (  # noqa: PLC0415
        DatabaseSink,
        SeedWriterMissingError,
        load_seed,
        writers_registered,
    )

    if not writers_registered():
        # Until P0-17 there is nothing to write to; previews still boot on an empty set.
        typer.echo("seed: no module registers seed writers yet; nothing loaded", err=True)
        return
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        typer.echo("DATABASE_URL is not set", err=True)
        raise typer.Exit(2)
    db.configure(database_url, os.environ.get("DATABASE_DIRECT_URL"))

    async def run() -> dict[str, int]:
        try:
            result = await load_seed(
                SEED_PATHS[set_name],
                DatabaseSink(),
                anchor=anchor.date() if anchor else None,
                clock=make_clock(),
            )
        finally:
            await db.dispose()
        return result.counts

    try:
        counts = asyncio.run(run())
    except SeedWriterMissingError as exc:
        typer.echo(f"seed: {exc} (the owning module has not landed yet)", err=True)
        raise typer.Exit(1) from exc
    typer.echo(", ".join(f"{n} {kind}" for kind, n in counts.items()))


class GenTarget(StrEnum):
    schemas = "schemas"
    openapi = "openapi"
    contract_tests = "contract-tests"
    all = "all"


@app.command()
def gen(
    what: Annotated[GenTarget, typer.Argument(help="What to generate")] = GenTarget.all,
    out: Annotated[
        Path | None, typer.Option(help="Repository root to write under (default: this repo)")
    ] = None,
    check: Annotated[
        bool, typer.Option("--check", help="Write nothing; exit 1 listing files that differ")
    ] = False,
) -> None:
    """Generate the JSON Schemas, the OpenAPI document and the schema contract tests from
    the code (P0-11); `make gen` then runs openapi-ts on the OpenAPI document."""
    from tumnis import gen as generator  # noqa: PLC0415

    root = (out or generator.REPO_ROOT).resolve()
    if check:
        differing = generator.check(root, what.value)
        for rel in differing:
            typer.echo(f"out of date: {rel}")
        if differing:
            typer.echo("run `make gen` and commit the result", err=True)
            raise typer.Exit(1)
        typer.echo(f"gen {what.value}: up to date")
        return
    written = generator.generate(root, what.value)
    typer.echo(f"gen {what.value}: wrote {len(written)} files under {root}")


class DrillModeOption(StrEnum):
    prod = "prod"
    rehearsal = "rehearsal"


def _aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise typer.BadParameter("needs a UTC offset, for example 2026-03-09T11:58:00+00:00")
    return parsed


@drill_app.command("record")
def drill_record(
    mode: Annotated[DrillModeOption, typer.Option(help="prod (quarterly, B2) or rehearsal")],
    rpo_seconds: Annotated[int, typer.Option(help="Archive time minus the marker time")],
    rto_seconds: Annotated[int, typer.Option(help="Restore start to checks passed")],
    target: Annotated[
        datetime, typer.Option(parser=_aware_datetime, help="The recovery target time (T_MARKER)")
    ],
) -> None:
    """Record a restore drill in ops_status (and, from P0-15, the audit log); exit 1 when
    the RPO (15 min) or the RTO (1 h) was missed."""
    from tumnis.core import db  # noqa: PLC0415
    from tumnis.core.drill import DrillResult, record_drill  # noqa: PLC0415

    settings = load_settings()
    db.configure(settings.database_direct_url, settings.database_direct_url, pooled=False)
    result = DrillResult(mode.value, rpo_seconds, rto_seconds, target)

    async def run() -> None:
        try:
            await record_drill(db.direct_engine(), result, now=make_clock().now())
        finally:
            await db.dispose()

    asyncio.run(run())
    typer.echo(
        f"drill {mode.value}: rpo {rpo_seconds}s, rto {rto_seconds}s, "
        f"{'ok' if result.ok else 'MISSED'}"
    )
    if not result.ok:
        raise typer.Exit(1)


@keys_app.command("rotate-master")
def rotate_master(
    to: Annotated[int, typer.Option("--to", help="Master key version to wrap every data key with")],
) -> None:
    """Re-wrap every workspace data key with master key version --to, as the owner role
    (DATABASE_OWNER_URL). The key file must hold the old versions and the new one; the
    sealed settings do not change (README, operate: master key rotation)."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: PLC0415
    from sqlalchemy.pool import NullPool  # noqa: PLC0415

    from tumnis.core.crypto import MasterKeyError, rewrap_all  # noqa: PLC0415

    settings = load_settings()
    owner_url = settings.database_owner_url
    if owner_url is None:
        _config_error("database_owner_url_missing: rotate-master runs as the owner role")
    try:
        master = settings.master_keys
    except MasterKeyError as exc:
        _config_error(exc)
    if to not in master.keys:
        _config_error(f"master key version {to} is not in {settings.master_key_file}")

    async def run() -> int:
        engine = create_async_engine(owner_url, poolclass=NullPool)
        try:
            async with async_sessionmaker(engine)() as session, session.begin():
                return await rewrap_all(session, master, to_version=to)
        finally:
            await engine.dispose()

    count = asyncio.run(run())
    typer.echo(f"re-wrapped {count} data keys under master key {to} ({master.fingerprint(to)})")


@audit_app.command("verify")
def audit_verify(
    workspace: Annotated[
        UUID | None, typer.Option(help="Verify only this workspace (default: every one)")
    ] = None,
) -> None:
    """Verify every workspace's audit hash chain and anchors (SEC-3); exit 1 and name the
    workspace, seq and kind of every break when one is broken."""
    from tumnis.core import db  # noqa: PLC0415
    from tumnis.core.audit import ChainBreak, verify_workspaces  # noqa: PLC0415

    settings = load_settings()
    db.configure(settings.database_direct_url, settings.database_direct_url, pooled=False)

    async def run() -> dict[UUID, list[ChainBreak]]:
        try:
            return await verify_workspaces([workspace] if workspace else None)
        finally:
            await db.dispose()

    results = asyncio.run(run())
    broken = [b for breaks in results.values() for b in breaks]
    for b in broken:
        typer.echo(f"audit chain broken: workspace {b.workspace_id} seq {b.seq} {b.kind}")
    bad = len({b.workspace_id for b in broken})
    typer.echo(f"audit verify: {len(results)} workspaces checked, {bad} broken")
    if broken:
        raise typer.Exit(1)
