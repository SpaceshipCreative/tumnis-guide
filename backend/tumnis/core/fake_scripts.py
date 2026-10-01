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

The fake runner (P2-04) also keeps the last `run` packet it received here, with a count
(`record_run_packet`, read by `GET /v1/test/fakes/runner/last-packet`); its task token is
redacted when its run ends (`redact_run_token`).
"""

from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import Column, Integer, Table, Text, cast, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert

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
# The last `run` packet the fake runner received and how many it received since the reset,
# stored as {"packet": {...}, "run_messages": n}. The packet keeps its live task token
# while its run is open, so a test can call back with it: a test-only exception to Scott's
# decision 31, which `redact_run_token` closes when the run ends.
LAST_PACKET = "runner.last_packet"
_PACKET_KEY = ""


async def record_run_packet(packet: Mapping[str, Any]) -> None:
    """Keep `packet` as the fake runner's last one and count it (one atomic upsert, so two
    dispatches at once count twice)."""
    first = {"packet": dict(packet), "run_messages": 1}
    stmt = insert(_t).values(adapter=LAST_PACKET, match_key=_PACKET_KEY, script=first)
    counted = func.jsonb_build_object(
        "packet",
        stmt.excluded.script["packet"],
        "run_messages",
        cast(_t.c.script["run_messages"].astext, Integer) + 1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[_t.c.adapter, _t.c.match_key], set_={"script": counted}
    )
    async with db.app_sessionmaker()() as session, session.begin():
        await session.execute(stmt)


async def last_run_packet() -> dict[str, Any] | None:
    """`{"packet": ..., "run_messages": n}`, or None before any packet (or while disabled)."""
    return await lookup(LAST_PACKET, _PACKET_KEY)


async def redact_run_token(run_id: object, marker: str) -> None:
    """The run ended: the stored last packet, when it is that run's, has its task token
    replaced by `marker`. Nothing while the store is disabled (every real deployment)."""
    if not _enabled:
        return
    token_path = literal(["packet", "callback", "task_token"], ARRAY(Text))
    stmt = (
        update(_t)
        .where(
            _t.c.adapter == LAST_PACKET,
            _t.c.match_key == _PACKET_KEY,
            _t.c.script["packet"]["run_id"].astext == str(run_id),
            _t.c.script["packet"]["callback"]["task_token"].astext.is_not(None),
        )
        .values(
            script=func.jsonb_set(
                _t.c.script, token_path, func.to_jsonb(cast(literal(marker), Text))
            )
        )
    )
    async with db.app_sessionmaker()() as session, session.begin():
        await session.execute(stmt)
