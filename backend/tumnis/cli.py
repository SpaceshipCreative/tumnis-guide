"""Command-line entry point: `tumnis api|worker|migrate|seed|...` (commands land in later WPs)."""

import asyncio
import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer

from tumnis.core.clock import Clock, SystemClock

app = typer.Typer(name="tumnis", help="Tumnis Guide backend.", no_args_is_help=True)

# The seed and load sets live beside the package in the source tree (backend/fixtures).
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class SeedSet(StrEnum):
    seed = "seed"
    load = "load"


SEED_PATHS = {SeedSet.seed: FIXTURES / "seed", SeedSet.load: FIXTURES / "load" / "load.yaml"}


def make_clock() -> Clock:
    """The CLI's clock; tests replace it with a FixedClock."""
    return SystemClock()


@app.callback()
def main() -> None:
    """Tumnis Guide backend."""


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
    from tumnis.core import db  # noqa: PLC0415
    from tumnis.seed import DatabaseSink, SeedWriterMissingError, load_seed  # noqa: PLC0415

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
