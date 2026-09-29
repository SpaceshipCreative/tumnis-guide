"""Alembic environment (P0-04, P0-06). Migrations run as the owner role; the URL comes from
the config (`sqlalchemy.url`, set by `tumnis migrate` and the test harness) or
DATABASE_OWNER_URL.

- Target metadata is `Base.metadata` with every module's models imported, so autogenerate
  sees all tables.
- Every run sets `lock_timeout` (plan default 5 s): a migration that would queue behind a
  long transaction fails fast instead of blocking the tables it waits on.
- Offline mode (`--sql`) renders SQL with literal values; squawk reads it
  (scripts/ci/squawk_migrations.py).
"""

import importlib
import os

from alembic import context
from sqlalchemy import create_engine, pool

from tumnis.core.base import Base
from tumnis.core.modules import MODULES

LOCK_TIMEOUT = "5s"

config = context.config

# Each module's models register on Base.metadata when imported. Dynamic imports: core never
# imports a module statically (import-linter core-below-modules).
for _module in MODULES:
    importlib.import_module(f"tumnis.modules.{_module}.models")
target_metadata = Base.metadata


def _url() -> str:
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_OWNER_URL")
    if not url:
        raise RuntimeError("set sqlalchemy.url or DATABASE_OWNER_URL to run migrations")
    return url


def _run() -> None:
    with context.begin_transaction():
        context.execute(f"SET lock_timeout = '{LOCK_TIMEOUT}'")
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    _run()


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        _run()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
