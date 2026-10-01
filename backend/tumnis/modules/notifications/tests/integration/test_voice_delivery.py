"""Spoken focus messages (P4-03, FR-10.8): with voice on for a level, a focus message at that
level reaches the focus bar marked to be spoken, with exactly the in-app text; the server
engine (Piper through the Speech slot) adds the clip to play. A level voice is not on for
speaks nothing, and Quiet fires nothing to speak.

Day: Tuesday 2026-03-10 in New York (the `workspace` fixture). The world is P2-15's focus
world (`Focus`), driven on the server clock with the in-process worker.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

import pytest

# P2-15's focus world, loaded by name: a module's tests import another module's `api` only.
_focus: Any = importlib.import_module("tumnis.modules.focus.tests.integration._focus")
TUESDAY, Focus, at = _focus.TUESDAY, _focus.Focus, _focus.at

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


@pytest.fixture
def focus(  # noqa: PLR0917  # the fixtures the world stands on
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
    dbos: Any,
) -> Iterator[Any]:
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    yield Focus(workspace, clock, db, dbos_sys_db, session_client, in_process=True)
    importlib.import_module("tumnis.modules.focus.workflows").use()


@pytest.fixture
def fake_tts() -> Iterator[Any]:
    """The Speech slot answering with a fresh FakeTTS as its Piper engine."""
    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    fake = importlib.import_module("tumnis.modules.decisions.adapters.speech.fake").FakeTTS()
    decisions.use_speech({"piper": fake})
    yield fake
    decisions.use_speech(None)


def _latest(current: dict[str, Any], kind: str) -> dict[str, Any]:
    found: list[dict[str, Any]] = [m for m in current["messages"] if m["kind"] == kind]
    return found[-1]


@pytest.mark.req("FR-10.8")
@pytest.mark.wp("P4-03")
async def test_spoken_text_equals_in_app_text(
    focus: Any, session_client: SessionClient, fake_tts: Any
) -> None:
    """T-P4-03-08
    Voice on at Nudge with the server engine: the Nudge block start reaches the bar with
    `speak: true` and a clip of exactly its in-app text. At Coach (voice off for it) the
    next block start is not spoken; at Quiet nothing fires, so nothing is spoken.
    """
    voice = {"enabled_levels": ["nudge"], "engine": "server", "server_provider": "piper"}
    saved = await session_client.put("/v1/settings/voice", json={"values": voice})
    assert saved.status_code == 200, saved.text

    first = await focus.task("Write proposal", first_action="Open the proposal outline")
    second = await focus.task("Fix footer link")
    third = await focus.task("Reply to Beta app")
    await focus.advance(at(TUESDAY, "08:30"))
    await focus.publish(
        TUESDAY, [(first, "10:00", "10:30"), (second, "11:00", "11:30"), (third, "12:00", "12:30")]
    )
    await focus.level("nudge")
    await focus.advance(at(TUESDAY, "10:00"))

    spoken = _latest(await focus.current(), "block_start")
    assert spoken["level"] == "nudge"
    assert spoken["speak"] is True
    assert fake_tts.calls == [spoken["message"]]  # the same text, nothing reworded
    assert "Open the proposal outline" in spoken["message"]
    assert spoken["clip_id"] is not None
    clip = await session_client.get(f"/v1/speech/clips/{spoken['clip_id']}")
    assert clip.status_code == 200
    assert clip.content == await fake_tts.synthesize(spoken["message"])
    heard = len(fake_tts.calls)

    await focus.level("coach")
    await focus.advance(at(TUESDAY, "11:00"))
    silent = _latest(await focus.current(), "block_start")
    assert silent["level"] == "coach"
    assert silent["speak"] is False
    assert silent["clip_id"] is None

    await focus.level("quiet")
    await focus.advance(at(TUESDAY, "12:00"))
    current = await focus.current()
    assert not [m for m in current["messages"] if m["fired_at"] > silent["fired_at"]]
    assert len(fake_tts.calls) == heard
