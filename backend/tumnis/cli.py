"""Command-line entry point: `tumnis api|worker|migrate|seed` (more commands in later WPs).

The image runs every process through this CLI. `api` and `worker` load the deployment
settings and run the boot checks first; a configuration error exits 78 (EX_CONFIG), so a
misconfigured preview never serves a request.
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError

from tumnis.core.clock import Clock, SystemClock
from tumnis.seed import SEED_PATHS, SeedSet
from tumnis.settings import EXIT_CONFIG, Settings, SettingsError

app = typer.Typer(name="tumnis", help="Tumnis Guide backend.", no_args_is_help=True)

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


def run_boot_checks(settings: Settings) -> None:
    from tumnis.settings import boot_checks  # noqa: PLC0415

    try:
        asyncio.run(boot_checks(settings))
    except SettingsError as exc:
        _config_error(exc)


@app.command()
def api(
    host: Annotated[str, typer.Option(help="Interface to bind")] = "0.0.0.0",  # noqa: S104  # inside the container only; no host port is published (FR-9.1)
    port: Annotated[int, typer.Option(help="Port to listen on")] = 8080,
) -> None:
    """Serve the HTTP API (and the built frontend) with uvicorn."""
    run_boot_checks(load_settings())
    import uvicorn  # noqa: PLC0415

    uvicorn.run("tumnis.app:create_app", factory=True, host=host, port=port, proxy_headers=True)


@app.command()
def worker() -> None:
    """Launch DBOS: queues, workflows and schedules."""
    settings = load_settings()
    run_boot_checks(settings)
    from tumnis.worker import main as worker_main  # noqa: PLC0415

    worker_main(settings)


@app.command()
def migrate(
    check: Annotated[
        bool, typer.Option("--check", help="Exit 1 unless the database is at every head")
    ] = False,
) -> None:
    """Upgrade the database to every Alembic head as the owner role (DATABASE_OWNER_URL)."""
    from tumnis.migrate import MigrationPendingError, upgrade, verify_at_heads  # noqa: PLC0415

    settings = load_settings()
    if settings.database_owner_url is None:
        _config_error("database_owner_url_missing: migrate runs as the owner role")
    try:
        if check:
            verify_at_heads(ALEMBIC_INI, settings.database_owner_url)
        else:
            upgrade(ALEMBIC_INI, settings)
    except SettingsError as exc:
        _config_error(exc)
    except MigrationPendingError as exc:
        typer.echo(f"tumnis: {exc}", err=True)
        raise typer.Exit(1) from exc


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
