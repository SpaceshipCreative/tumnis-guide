"""`tumnis migrate`: Alembic `upgrade heads` as the owner role, then the deployment marker.

A database already marked for another deployment is refused before any revision runs, so a
preview's migrate step can never alter the production schema. A database with no marker
yet is stamped with this deployment's env and master key fingerprint after the upgrade.

Version skew (P0-30, REL-4): after a rollback from N+1 to N the database carries revisions
N's script directory does not know. N+1's migrations are expand-only, so its schema serves
N; migrate leaves such a database alone and readiness treats it as ready.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, create_engine, inspect, text
from sqlalchemy.pool import NullPool

from tumnis.settings import Marker, Settings, check_markers, master_key_fingerprint

MARKER_TABLE = "deployment_marker"
ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


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


class DbPosition(StrEnum):
    AT_HEAD = "at_head"
    BEHIND = "behind"
    AHEAD = "ahead"


@dataclass(frozen=True)
class ReleaseRevisions:
    """The revisions this release's script directory knows, its heads, and what each
    known revision implies is applied: itself and its ancestors through `down_revision`
    and `depends_on` alike."""

    heads: frozenset[str]
    known: frozenset[str]
    reaches: Mapping[str, frozenset[str]] = field(compare=False, repr=False)

    @classmethod
    def of(cls, script: ScriptDirectory) -> "ReleaseRevisions":
        known = frozenset(rev.revision for rev in script.walk_revisions())
        # Alembic's own upgrade reckoning (dependencies included): what `upgrade <rev>`
        # would run on an empty database.
        reaches = {
            rev: frozenset(r.revision for r in script.iterate_revisions(rev, "base"))
            for rev in known
        }
        return cls(heads=frozenset(script.get_heads()), known=known, reaches=reaches)

    def position(self, current: set[str]) -> DbPosition:
        """AHEAD when any current revision is unknown to this release (a later release
        ran its migrations: a rollback); AT_HEAD when the current revisions reach every
        head of this release; BEHIND otherwise (an empty database included).

        `alembic_version` holds only the leaves Alembic keeps: after `upgrade heads` a
        head that other branches depend on (integrations_0001 under calendar_0001 and
        knowledge_0001) is implied by its dependents, not stored, so the rows are compared
        by what they reach, never to the script heads as strings."""
        if current - self.known:
            return DbPosition.AHEAD
        reached = frozenset().union(*(self.reaches[rev] for rev in current))
        if self.heads <= reached:
            return DbPosition.AT_HEAD
        return DbPosition.BEHIND


def db_position(current: set[str], script: ScriptDirectory) -> DbPosition:
    return ReleaseRevisions.of(script).position(current)


@cache
def release_revisions(ini: Path = ALEMBIC_INI) -> ReleaseRevisions:
    """This release's revisions, read once per process (readiness asks on every probe)."""
    return ReleaseRevisions.of(ScriptDirectory.from_config(Config(str(ini))))


def current_revisions(conn: Connection) -> set[str]:
    return set(MigrationContext.configure(conn).get_current_heads())


def upgrade(ini: Path, settings: Settings) -> DbPosition:
    """Upgrade a database BEHIND this release to its heads (expand revisions only, REL-4);
    leave one AT_HEAD or AHEAD alone. Returns where the database was."""
    assert settings.database_owner_url is not None  # noqa: S101  # checked by the CLI
    cfg = alembic_config(ini, settings.database_owner_url)
    engine = create_engine(settings.database_owner_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            markers = read_markers(conn)
            current = current_revisions(conn)
        if markers:
            check_markers(settings, markers)
        position = db_position(current, ScriptDirectory.from_config(cfg))
        if position is DbPosition.AHEAD:
            return position
        if position is DbPosition.BEHIND:
            command.upgrade(cfg, "heads")
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
    return position


def verify_at_heads(ini: Path, owner_url: str) -> None:
    """MigrationPendingError unless the database is at this release's heads (behind or
    ahead raises)."""
    script = ScriptDirectory.from_config(alembic_config(ini, owner_url))
    engine = create_engine(owner_url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            current = current_revisions(conn)
    finally:
        engine.dispose()
    if db_position(current, script) is not DbPosition.AT_HEAD:
        heads = sorted(script.get_heads())
        raise MigrationPendingError(f"database at {sorted(current)}, code at {heads}")
