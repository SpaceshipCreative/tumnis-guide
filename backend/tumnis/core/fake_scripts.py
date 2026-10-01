"""Fake scripts shared across processes (test-only; R-37, `POST /v1/test/fakes/{adapter}/script`).

compose.test runs the api and the worker as separate containers, so a script held in one
process's fake never reaches the fake the other process asks. The test route stores the
script here, in Postgres, and each scriptable fake looks it up on every call while the
store is enabled: the api's lifespan and the worker's `main` enable it when adapters are
fakes, so unit tests and real deployments never read it. `POST /v1/test/reset` empties the
table with every other one.

A scriptable fake registers a parser under its hook name (`decisions.jev`, `generation`,
...). The parser validates a posted body (raising `ValueError`, pydantic's
`ValidationError` included) and returns the match key the fake looks the script up by (a
decision point, say; `""` for one script per hook) and the JSON to store. `lookup` prefers
the exact key and falls back to `""`.
"""

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import Column, Table, Text, literal, or_, select
from sqlalchemy.dialects.postgresql import JSONB, insert

from tumnis.core import db
from tumnis.core.base import Base

Parser = Callable[[Mapping[str, Any]], tuple[str, dict[str, Any]]]

# Mirrors revision core_0008_fake_scripts (the migration creates it; this is for queries).
fake_scripts = Table(
    "fake_scripts",
    Base.metadata,
    Column("adapter", Text, primary_key=True),
    Column("match_key", Text, primary_key=True),
    Column("script", JSONB, nullable=False),
)
_t = fake_scripts

_PARSERS: dict[str, Parser] = {}
_enabled = False


def register_fake_script(name: str, parse: Parser) -> None:
    """Make hook `name` scriptable through the test route; one parser per name."""
    if name in _PARSERS:
        raise ValueError(f"fake script {name!r} is already registered")
    _PARSERS[name] = parse


def parser(name: str) -> Parser | None:
    return _PARSERS.get(name)


def enable() -> None:
    """Fakes in this process read stored scripts from now on (fakes mode only)."""
    global _enabled  # noqa: PLW0603  # one switch per process
    _enabled = True


def disable() -> None:
    global _enabled  # noqa: PLW0603  # one switch per process
    _enabled = False


def enabled() -> bool:
    return _enabled


async def put(name: str, key: str, script: Mapping[str, Any]) -> None:
    """Store `script` for (name, key), replacing an earlier one."""
    stmt = insert(_t).values(adapter=name, match_key=key, script=dict(script))
    stmt = stmt.on_conflict_do_update(
        index_elements=[_t.c.adapter, _t.c.match_key], set_={"script": stmt.excluded.script}
    )
    async with db.app_sessionmaker()() as session, session.begin():
        await session.execute(stmt)


async def lookup(name: str, key: str = "") -> dict[str, Any] | None:
    """The script stored for (name, key), else for (name, ""); None while disabled."""
    if not _enabled:
        return None
    stmt = (
        select(_t.c.script)
        .where(_t.c.adapter == name, or_(_t.c.match_key == key, _t.c.match_key == ""))
        .order_by((_t.c.match_key == literal(key)).desc())
        .limit(1)
    )
    async with db.app_sessionmaker()() as session, session.begin():
        script: dict[str, Any] | None = await session.scalar(stmt)
    return script


# --- The fake runner's last packet (P2-04, `GET /v1/test/fakes/runner/last-packet`) ---------

RUNNER = "runner"  # the fake runner's scripts (`POST /v1/test/fakes/runner/script`)
LAST_PACKET = "runner.last_packet"  # the last `run` packet it received, and how many


async def record_run_packet(packet: Mapping[str, Any]) -> None:
    """Keep `packet` as the fake runner's last one and count it."""
    raise NotImplementedError


async def last_run_packet() -> dict[str, Any] | None:
    """`{"packet": ..., "run_messages": n}`, or None before any packet (or while disabled)."""
    raise NotImplementedError
