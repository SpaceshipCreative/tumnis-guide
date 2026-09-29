"""Where the database stands against this release's Alembic heads (P0-30, REL-4)."""

from __future__ import annotations

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from tumnis.migrate import ALEMBIC_INI, DbPosition, db_position, release_revisions


@pytest.fixture(scope="module")
def script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


@pytest.mark.req("REL-4")
@pytest.mark.wp("P0-30")
def test_position_against_this_release(script: ScriptDirectory) -> None:
    """At the heads: at_head; missing a head (or empty): behind; any unknown revision,
    even beside older known ones: ahead (a later release migrated, then rolled back)."""
    heads = set(script.get_heads())
    some_head = sorted(heads)[0]
    older = script.get_revision(some_head).down_revision

    assert db_position(heads, script) is DbPosition.AT_HEAD
    assert db_position(set(), script) is DbPosition.BEHIND
    assert db_position(heads - {some_head}, script) is DbPosition.BEHIND
    if isinstance(older, str):
        assert db_position((heads - {some_head}) | {older}, script) is DbPosition.BEHIND
    assert db_position(heads | {"zz_next_release_0001"}, script) is DbPosition.AHEAD
    assert db_position({"zz_next_release_0001"}, script) is DbPosition.AHEAD
    assert release_revisions().position(heads) is DbPosition.AT_HEAD
