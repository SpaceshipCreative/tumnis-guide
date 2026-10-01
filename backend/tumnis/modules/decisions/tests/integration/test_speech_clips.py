"""Server-side speech clips (P4-03, FR-11.7, SEC-10): a focus message spoken through the
Speech slot is kept as a WAV clip for `CLIP_TTL_MIN` minutes and served only through
`GET /v1/speech/clips/{id}` to a signed-in member of its workspace."""

from __future__ import annotations

import importlib
from datetime import timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from tests.fixtures import make_workspace

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

TEXT = "Time for Write proposal. First step: open the proposal outline."


@pytest.fixture
def fake_tts() -> Iterator[Any]:
    """The Speech slot answering with a fresh FakeTTS as its Piper engine."""
    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    fake = importlib.import_module("tumnis.modules.decisions.adapters.speech.fake").FakeTTS()
    decisions.use_speech({"piper": fake})
    yield fake
    decisions.use_speech(None)


async def _server_voice(workspace_id: Any) -> Any:
    """Voice on at Nudge through the server engine (Piper) for this workspace; its context."""
    from tumnis.core.settings_store import put_setting  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")

    ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
    settings = decisions.VoiceSettings(enabled_levels={"nudge"}, engine="server")
    await put_setting(ctx, decisions.VOICE_SECTION, settings, expected_version=None)
    return ctx


@pytest.mark.req("FR-11.7")
@pytest.mark.wp("P4-03")
@pytest.mark.xfail(strict=True, reason="spec:P4-03")
async def test_clip_served_and_expires(  # noqa: PLR0917  # the fixtures it stands on
    core_db: None,
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
    fake_tts: Any,
) -> None:
    """T-P4-03-09
    A clip spoken for a message is served as `audio/wav` (the engine's bytes, nosniff) to a
    session of its workspace; after `CLIP_TTL_MIN` minutes it is 404, and another
    workspace's clip is 404 from the start.
    """
    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")

    ctx = await _server_voice(workspace.id)
    clip = await decisions.speak(ctx, TEXT, message_id=uuid4(), now=clock.now())
    assert clip is not None
    assert fake_tts.calls == [TEXT]

    served = await session_client.get(f"/v1/speech/clips/{clip.id}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "audio/wav"
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == await fake_tts.synthesize(TEXT)

    other = await _server_voice(make_workspace(db, "Other"))
    theirs = await decisions.speak(other, TEXT, message_id=uuid4(), now=clock.now())
    assert theirs is not None
    assert (await session_client.get(f"/v1/speech/clips/{theirs.id}")).status_code == 404

    later = clock.now() + timedelta(minutes=decisions.CLIP_TTL_MIN, seconds=1)
    await session_client.post("/v1/test/clock", json={"time": later.isoformat()})
    assert (await session_client.get(f"/v1/speech/clips/{clip.id}")).status_code == 404
