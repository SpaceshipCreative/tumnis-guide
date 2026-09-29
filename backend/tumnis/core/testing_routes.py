"""Test-only routes (P0-04), mounted by create_app only when TUMNIS_ADAPTERS=fake; the
preview guard guarantees previews always run with fakes, so real deployments never have
them. `POST /v1/test/reset` empties the database and reloads a seed set."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tumnis.seed import SEED_PATHS, DatabaseSink, SeedSet, load_seed, writers_registered

router = APIRouter(prefix="/v1/test", tags=["test"])

# Deployment-level tables a reset keeps: the marker says which deployment this database is.
KEEP_TABLES = frozenset({"deployment_marker"})


async def truncate_tables(owner_url: str) -> list[str]:
    """TRUNCATE every table in `public` but the kept ones and Alembic's, as the owner."""
    engine = create_async_engine(owner_url, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            names = [
                name
                for (name,) in await conn.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
                if name not in KEEP_TABLES and not name.startswith("alembic_version")
            ]
            if names:
                quote = conn.dialect.identifier_preparer.quote
                listed = ", ".join(quote(name) for name in sorted(names))
                await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))
    finally:
        await engine.dispose()
    return names


@router.post("/reset", status_code=204)
async def reset(
    request: Request, seed_set: Annotated[SeedSet, Query(alias="set")] = SeedSet.seed
) -> Response:
    settings = request.app.state.settings
    if settings.database_owner_url is None:
        raise HTTPException(status_code=500, detail="reset needs DATABASE_OWNER_URL")
    await truncate_tables(settings.database_owner_url)
    if writers_registered():  # from P0-17 on; before that the seed has nowhere to go
        await load_seed(SEED_PATHS[seed_set], DatabaseSink(), clock=request.app.state.clock)
    return Response(status_code=204)
