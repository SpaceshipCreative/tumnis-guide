"""Hosted provider keys stay in the server's .env (Scott decision 75; FR-11.7, FR-11.10,
SEC-6): a focus message spoken through the hosted provider, configured with its key from
Settings, leaves that key in no table, no API answer and no log line. With the hosted
provider chosen but no key, the slot is `not_configured`: nothing is spoken and nothing
fails."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import SecretStr
from structlog.testing import capture_logs

from tests._pg import OWNER

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import Fakes, WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SPEECH_KEY = "hosted-speech-test-not-a-key"
EMBED_KEY = "hosted-embed-test-not-a-key"
HOSTED_URL = "http://10.20.0.7:8080"
TEXT = "Time for Write proposal. First step: open the proposal outline."


@pytest.fixture
def slots(fakes: Fakes) -> Iterator[Any]:
    """`decisions.api` in fakes mode, both slots put back to their defaults afterwards."""
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.settings import EmbeddingsSettings, SpeechSettings  # noqa: PLC0415

    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    decisions.use_speech(None)
    yield decisions
    policy = NetPolicy(mode="self-hosted")
    decisions.configure_speech(SpeechSettings(), net_policy=policy)
    decisions.configure_embeddings(EmbeddingsSettings(), net_policy=policy)


async def _hosted_voice(workspace_id: Any) -> Any:
    """Voice on at Nudge through the hosted server provider (allowed) for this workspace."""
    from tumnis.core.settings_store import put_setting  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
    voice = decisions.VoiceSettings(
        enabled_levels={"nudge"}, engine="server", server_provider="hosted", hosted_allowed=True
    )
    await put_setting(ctx, decisions.VOICE_SECTION, voice, expected_version=None)
    return ctx


def _tables_holding(db: DbUrls, needle: str) -> list[str]:
    """Every table (outside the system schemas) with a row whose text, or any of whose bytea
    columns, contains `needle`; read as the owner, so row-level security hides nothing."""
    import psycopg  # noqa: PLC0415
    from psycopg import sql  # noqa: PLC0415

    found: list[str] = []
    with psycopg.connect(db.libpq(OWNER), autocommit=True) as conn:
        tables = conn.execute(
            """
            SELECT table_schema, table_name FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            """
        ).fetchall()
        for schema, table in tables:
            name = sql.Identifier(schema, table)
            checks: list[tuple[sql.Composed, tuple[object, ...]]] = [
                (
                    sql.SQL("SELECT EXISTS (SELECT 1 FROM {} t WHERE t::text LIKE %s)").format(
                        name
                    ),
                    (f"%{needle}%",),
                )
            ]
            byteas = conn.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = %s AND table_name = %s AND data_type = 'bytea'
                """,
                (schema, table),
            ).fetchall()
            checks += [
                (
                    sql.SQL(
                        "SELECT EXISTS (SELECT 1 FROM {} WHERE position(%s::bytea IN {}) > 0)"
                    ).format(name, sql.Identifier(column)),
                    (needle.encode(),),
                )
                for (column,) in byteas
            ]
            if any(conn.execute(query, params).fetchone() == (True,) for query, params in checks):
                found.append(f"{schema}.{table}")
    return found


@pytest.mark.req("FR-11.7", "FR-11.10", "SEC-6")
@pytest.mark.wp("P4-03")
async def test_hosted_keys_never_reach_db_responses_or_logs(  # noqa: PLR0917  # its fixtures
    core_db: None,
    db: DbUrls,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    session_client: SessionClient,
    slots: Any,
) -> None:
    """Both hosted keys configured from Settings; a message spoken by the hosted provider and
    a text embedded by the hosted embedder: no table holds either key, the clip and the
    voice and embeddings settings answers do not carry it, and no log line does."""
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.core.tenancy import use_workspace  # noqa: PLC0415

    config: Any = importlib.import_module("tumnis.settings")
    policy = NetPolicy(mode="self-hosted")
    speech = config.SpeechSettings(
        hosted_base_url=HOSTED_URL, hosted_model="tts-1", hosted_api_key=SecretStr(SPEECH_KEY)
    )
    embeddings = config.EmbeddingsSettings(
        hosted_base_url=HOSTED_URL,
        hosted_model="text-embedding-3-small",
        hosted_dims=8,
        hosted_api_key=SecretStr(EMBED_KEY),
    )
    ctx = await _hosted_voice(workspace.id)
    with capture_logs() as logs:
        slots.configure_speech(speech, net_policy=policy)
        slots.configure_embeddings(embeddings, net_policy=policy)
        assert slots.hosted_speech_state() == "configured"
        assert slots.hosted_embeddings_state() == "configured"
        clip = await slots.speak(ctx, TEXT, message_id=uuid4(), now=clock.now())
        with use_workspace(ctx):
            embedded = await slots.embed(["a passage to embed"], None)

    assert clip is not None
    assert clip.provider == "hosted"
    assert embedded.hosted is True
    assert not embedded.skipped

    answers = [
        await session_client.get(f"/v1/speech/clips/{clip.id}"),
        await session_client.get("/v1/settings/voice"),
        await session_client.get("/v1/settings/embeddings"),
    ]
    assert answers[0].status_code == 200
    for answer in answers:
        for key in (SPEECH_KEY, EMBED_KEY):
            assert key.encode() not in answer.content
            assert key not in json.dumps(dict(answer.headers))
    for key in (SPEECH_KEY, EMBED_KEY):
        assert key not in json.dumps(logs, default=str)
        assert _tables_holding(db, key) == []


@pytest.mark.req("FR-11.7", "SEC-6")
@pytest.mark.wp("P4-03")
@pytest.mark.xfail(strict=True, reason="spec:P4-03")
async def test_hosted_speech_without_key_speaks_nothing(
    core_db: None,
    master_key_file: Any,  # the voice setting is sealed with the workspace data key
    workspace: WorkspaceHandle,
    clock: FixedClock,
    slots: Any,
) -> None:
    """The workspace chose the hosted provider but the server has no
    `SPEECH__HOSTED_API_KEY`: `speak` makes no clip and raises nothing, and says the hosted
    route is not configured."""
    from tumnis.core.net import NetPolicy  # noqa: PLC0415

    config: Any = importlib.import_module("tumnis.settings")
    ctx = await _hosted_voice(workspace.id)
    slots.configure_speech(
        config.SpeechSettings(hosted_base_url=HOSTED_URL, hosted_model="tts-1"),
        net_policy=NetPolicy(mode="self-hosted"),
    )
    assert slots.hosted_speech_state() == "not_configured"
    with capture_logs() as logs:
        clip = await slots.speak(ctx, TEXT, message_id=uuid4(), now=clock.now())
    assert clip is None
    events = [e for e in logs if e["event"] == "decisions.speech_not_configured"]
    assert [e["route"] for e in events] == ["hosted"]
