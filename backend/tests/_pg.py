"""Postgres helpers for the harness: role bootstrap, template build, clone and drop.

One container per xdist worker; one template per worker, migrated to head; one clone per
test. `CREATE DATABASE ... TEMPLATE` refuses a template anyone is connected to, which is
why the template is closed to connections once migrated.

The roles and the per-database setup come from the same files compose runs on first start
(deploy/postgres/initdb, P0-06): 01-roles.sh's SQL with test passwords, then
02-database.sql as the superuser on every database the harness builds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql

BACKEND = Path(__file__).resolve().parents[1]
INITDB = BACKEND.parent / "deploy" / "postgres" / "initdb"
ROLES_SH = INITDB / "01-roles.sh"
DATABASE_SQL = INITDB / "02-database.sql"
ALEMBIC_INI = BACKEND / "alembic.ini"
# Test-only revisions (the harness_probe and tenant_probe tables) join every test template.
TEST_VERSION_LOCATIONS = (BACKEND / "tests" / "harness" / "migrations",)

OWNER, APP, SUPERUSER = "tumnis_owner", "tumnis_app", "postgres"
PASSWORDS = {OWNER: "owner-test", APP: "app-test", SUPERUSER: "postgres"}

_HEREDOC = re.compile(r"<<'SQL'\n(.*?)\nSQL\n", re.DOTALL)
_PSQL_VAR = re.compile(r":'(\w+)'")


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


def roles_sql(passwords: dict[str, str]) -> list[str]:
    """The statements of 01-roles.sh's psql heredoc, psql variables (:'owner_password',
    :'app_password') bound as literals, one per item (CREATE DATABASE cannot share a
    transaction)."""
    match = _HEREDOC.search(ROLES_SH.read_text())
    if match is None:
        raise ValueError(f"no <<'SQL' heredoc in {ROLES_SH}")
    values = {"owner_password": passwords[OWNER], "app_password": passwords[APP]}

    def bind(var: re.Match[str]) -> str:
        return "'" + values[var.group(1)].replace("'", "''") + "'"

    body = _PSQL_VAR.sub(bind, match.group(1))
    return [
        statement.strip() for statement in re.split(r";\s*$", body, flags=re.M) if statement.strip()
    ]


def bootstrap_roles(superuser_dsn: str) -> None:
    """Run 01-roles.sh's SQL with the test passwords (the same file compose runs)."""
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        for statement in roles_sql(PASSWORDS):
            conn.execute(statement.encode())


def prepare_database(base: DbUrls, name: str) -> None:
    """02-database.sql as the superuser on `name` (its \\connect line is compose's)."""
    script = "\n".join(
        line for line in DATABASE_SQL.read_text().splitlines() if not line.startswith("\\")
    )
    with psycopg.connect(DbUrls(base.host, base.port, name).libpq(SUPERUSER)) as conn:
        conn.execute(script.encode())
        conn.commit()


def alembic_config(url: str) -> Config:
    """alembic.ini's config (every module's version location) plus the test-only ones."""
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    configured = cfg.get_main_option("version_locations")
    # alembic.ini sets path_separator = newline (P0-06): one location per line.
    locations = [*(configured.split() if configured else []), *map(str, TEST_VERSION_LOCATIONS)]
    cfg.set_main_option("version_locations", "\n".join(locations))
    return cfg


def create_database(base: DbUrls, name: str) -> DbUrls:
    """An empty database owned by tumnis_owner, prepared like `tumnis` (02-database.sql)."""
    with psycopg.connect(base.libpq(OWNER), autocommit=True) as conn:
        conn.execute(
            sql.SQL("CREATE DATABASE {} OWNER {}").format(
                sql.Identifier(name), sql.Identifier(OWNER)
            )
        )
    prepare_database(base, name)
    return DbUrls(base.host, base.port, name)


def build_template(base: DbUrls, template: str) -> None:
    """CREATE DATABASE <template> OWNER tumnis_owner; 02-database.sql as the superuser;
    alembic upgrade heads as owner; mark it a `dev` deployment (P0-04); ALTER DATABASE
    <template> WITH ALLOW_CONNECTIONS false IS_TEMPLATE true."""
    template_urls = create_database(base, template)
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


def schema_dump(container: object, name: str) -> str:
    """`pg_dump --schema-only` of `name`, run inside the Postgres container as the superuser.
    pg_dump 18 frames its output with \\restrict/\\unrestrict and a random key; those lines
    are dropped so two dumps of the same schema compare equal."""
    result = container.exec(  # type: ignore[attr-defined]
        ["pg_dump", "--schema-only", "-U", SUPERUSER, "-d", name]
    )
    if result.exit_code != 0:
        raise RuntimeError(f"pg_dump failed: {result.output.decode()}")
    lines = result.output.decode().splitlines()
    return "\n".join(line for line in lines if not line.startswith(("\\restrict", "\\unrestrict")))
