"""Postgres helpers for the harness: role bootstrap, template build, clone and drop.

One container per xdist worker; one template per worker, migrated to head; one clone per
test. `CREATE DATABASE ... TEMPLATE` refuses a template anyone is connected to, which is
why the template is closed to connections once migrated.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

BACKEND = Path(__file__).resolve().parents[1]
ROLES_SQL = BACKEND.parent / "deploy" / "postgres" / "initdb" / "01-roles.sql"
ALEMBIC_INI = BACKEND / "alembic.ini"
# Test-only revisions (the harness_probe scratch table) join every test template.
TEST_VERSION_LOCATIONS = (BACKEND / "tests" / "harness" / "migrations",)

OWNER, APP = "tumnis_owner", "tumnis_app"
PASSWORDS = {OWNER: "owner-test", APP: "app-test"}


@dataclass(frozen=True)
class DbUrls:
    host: str
    port: int
    name: str

    def url(self, role: str, driver: str = "psycopg") -> str:
        return f"postgresql+{driver}://{role}:{PASSWORDS[role]}@{self.host}:{self.port}/{self.name}"

    @property
    def owner(self) -> str:
        return self.url(OWNER)

    @property
    def app(self) -> str:
        return self.url(APP)

    def libpq(self, role: str) -> str:
        return f"postgresql://{role}:{PASSWORDS[role]}@{self.host}:{self.port}/{self.name}"


def bootstrap_roles(superuser_dsn: str) -> None:
    """Run deploy/postgres/initdb/01-roles.sql with test passwords (same file as compose)."""
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        conn.execute("SELECT set_config('tumnis.owner_password', %s, false)", (PASSWORDS[OWNER],))
        conn.execute("SELECT set_config('tumnis.app_password', %s, false)", (PASSWORDS[APP],))
        conn.execute(ROLES_SQL.read_text())  # type: ignore[arg-type]  # trusted repo file


def alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url)
    configured = cfg.get_main_option("version_locations")
    # alembic.ini sets path_separator = os, so locations join with os.pathsep, not spaces.
    locations = [
        *(configured.split(os.pathsep) if configured else []),
        *map(str, TEST_VERSION_LOCATIONS),
    ]
    cfg.set_main_option("version_locations", os.pathsep.join(locations))
    return cfg


def build_template(base: DbUrls, template: str) -> None:
    """CREATE DATABASE <template> OWNER tumnis_owner; alembic upgrade heads as owner; mark it
    a `dev` deployment (P0-04); ALTER DATABASE <template> WITH ALLOW_CONNECTIONS false
    IS_TEMPLATE true."""
    with psycopg.connect(base.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(template), sql.Identifier(OWNER)
            )
        )
    template_urls = DbUrls(base.host, base.port, template)
    command.upgrade(alembic_config(template_urls.owner), "heads")
    with psycopg.connect(template_urls.libpq(OWNER), autocommit=True) as conn:
        conn.execute("INSERT INTO deployment_marker (env) VALUES ('dev')")
    with psycopg.connect(base.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS false IS_TEMPLATE true").format(
                sql.Identifier(template)
            )
        )


def clone(base: DbUrls, template: str, name: str) -> DbUrls:
    with psycopg.connect(base.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} TEMPLATE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(template), sql.Identifier(OWNER)
            )
        )
    return DbUrls(base.host, base.port, name)


def drop(base: DbUrls, name: str) -> None:
    with psycopg.connect(base.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
