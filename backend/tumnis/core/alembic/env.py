"""Alembic environment. Migrations run as the owner role; the URL comes from the config
(`sqlalchemy.url`, set by `tumnis migrate` and the test harness) or DATABASE_OWNER_URL.
P0-06 adds target metadata and the per-module version locations."""

import os

from alembic import context
from sqlalchemy import Connection, create_engine, pool

config = context.config


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_OWNER_URL")
    if not url:
        raise RuntimeError("set sqlalchemy.url or DATABASE_OWNER_URL to run migrations")
    return url


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        _run(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
