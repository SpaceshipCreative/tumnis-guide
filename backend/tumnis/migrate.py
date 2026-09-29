"""`tumnis migrate`: Alembic `upgrade heads` as the owner role, then the deployment marker.

A database already marked for another deployment is refused before any revision runs, so a
preview's migrate step can never alter the production schema. A database with no marker
yet is stamped with this deployment's env and master key fingerprint after the upgrade.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tumnis.settings import Marker, Settings, check_markers, master_key_fingerprint

MARKER_TABLE = "deployment_marker"


class MigrationPendingError(RuntimeError):
    """The database is not at every head (`tumnis migrate --check`)."""


def alembic_config(ini: Path, owner_url: str) -> Config:
    cfg = Config(str(ini))
    cfg.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    return cfg


def read_markers(conn: Connection) -> list[Marker] | None:
    """Every marker row, or None when the table does not exist yet."""
    if not inspect(conn).has_table(MARKER_TABLE):
        return None
    rows = conn.execute(text("SELECT env, master_key_fingerprint FROM deployment_marker"))
    return [Marker(env, fingerprint) for env, fingerprint in rows]


def upgrade(ini: Path, settings: Settings) -> None:
    assert settings.database_owner_url is not None  # noqa: S101  # checked by the CLI
    engine = create_engine(settings.database_owner_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            markers = read_markers(conn)
        if markers:
            check_markers(settings, markers)
        command.upgrade(alembic_config(ini, settings.database_owner_url), "heads")
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO deployment_marker (env, master_key_fingerprint) "
                    "SELECT :env, :fingerprint "
                    "WHERE NOT EXISTS (SELECT 1 FROM deployment_marker)"
                ),
                {
                    "env": settings.deployment_env,
                    "fingerprint": master_key_fingerprint(settings.master_key_file),
                },
            )
    finally:
        engine.dispose()


def verify_at_heads(ini: Path, owner_url: str) -> None:
    heads = set(ScriptDirectory.from_config(alembic_config(ini, owner_url)).get_heads())
    engine = create_engine(owner_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            current = set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()
    if current != heads:
        raise MigrationPendingError(f"database at {sorted(current)}, code at {sorted(heads)}")
