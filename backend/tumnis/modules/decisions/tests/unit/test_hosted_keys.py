"""Hosted provider keys come from the server's .env (Scott decision 75; FR-11.7, FR-11.10,
SEC-6): the hosted speech provider (P4-03) and the hosted embedder (P3-10) read their API
keys from the deployment's environment through `Settings`, never from the database. A
blank variable counts as unset; with no key the slot reports `not_configured` and builds no
hosted engine; a preview refuses either key; the key never shows in a dump, a repr or a log
line, and the provider-config writer refuses to seal one for those slots."""

from __future__ import annotations

import importlib
import json
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from structlog.testing import capture_logs

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

DSN = "postgresql+psycopg://tumnis_app:pw@127.0.0.1:5432/tumnis"
SPEECH_KEY = "hosted-speech-test-not-a-key"
EMBED_KEY = "hosted-embed-test-not-a-key"
HOSTED_URL = "http://10.20.0.7:8080"  # an OpenAI-compatible server in the test (no socket)
LOCAL_URL = "http://10.20.0.6:8000"
TEXT = "Time for Write proposal. First step: open the proposal outline."
# Every variable these tests set or that would change what Settings builds.
ENV = (
    "DEPLOYMENT_ENV",
    "TUMNIS_ADAPTERS",
    "TYPESAFE_API_KEY",
    "SPEECH__PIPER_URL",
    "SPEECH__PIPER_VOICE",
    "SPEECH__HOSTED_BASE_URL",
    "SPEECH__HOSTED_MODEL",
    "SPEECH__HOSTED_VOICE",
    "SPEECH__HOSTED_API_KEY",
    "EMBEDDINGS__BASE_URL",
    "EMBEDDINGS__MODEL",
    "EMBEDDINGS__DIMS",
    "EMBEDDINGS__HOSTED_BASE_URL",
    "EMBEDDINGS__HOSTED_MODEL",
    "EMBEDDINGS__HOSTED_DIMS",
    "EMBEDDINGS__HOSTED_API_KEY",
)


def _settings(monkeypatch: pytest.MonkeyPatch, **values: str) -> Any:
    """`Settings()` read from an environment holding only the database URLs and `values`."""
    from tumnis.settings import Settings  # noqa: PLC0415

    for name in ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("DATABASE_DIRECT_URL", DSN)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings()


HOSTED_SPEECH = {
    "SPEECH__HOSTED_BASE_URL": HOSTED_URL,
    "SPEECH__HOSTED_MODEL": "tts-1",
    "SPEECH__HOSTED_API_KEY": SPEECH_KEY,
}
HOSTED_EMBED = {
    "EMBEDDINGS__HOSTED_BASE_URL": HOSTED_URL,
    "EMBEDDINGS__HOSTED_MODEL": "text-embedding-3-small",
    "EMBEDDINGS__HOSTED_DIMS": "8",
    "EMBEDDINGS__HOSTED_API_KEY": EMBED_KEY,
}


@pytest.fixture
def slots() -> Iterator[Any]:
    """`decisions.api`, with both slots put back to their defaults afterwards."""
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.settings import EmbeddingsSettings, SpeechSettings  # noqa: PLC0415

    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    yield decisions
    policy = NetPolicy(mode="self-hosted")
    decisions.configure_speech(SpeechSettings(), net_policy=policy)
    decisions.configure_embeddings(EmbeddingsSettings(), net_policy=policy)


def _resolve_with(
    monkeypatch: pytest.MonkeyPatch, module: str, transport: httpx.MockTransport
) -> list[tuple[str, dict[str, Any]]]:
    """Make `module`'s registry lookups build the real adapters on `transport` (no socket);
    the list records each lookup's adapter name and dependencies."""
    from tumnis.core.adapters import registry  # noqa: PLC0415

    seen: list[tuple[str, dict[str, Any]]] = []

    def resolve(name: str, mode: Any, **deps: Any) -> Any:
        seen.append((name, deps))
        return registry.resolve(name, mode, transport=transport, **deps)

    monkeypatch.setattr(importlib.import_module(module), "resolve", resolve)
    monkeypatch.setenv("TUMNIS_ADAPTERS", "real")
    return seen


def _recorder(
    answer: Callable[[httpx.Request], httpx.Response],
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return answer(request)

    return httpx.MockTransport(handler), requests


def _no_key_in(key: str, logs: list[Any], *things: Any) -> None:
    assert key not in json.dumps(logs, default=str)
    for thing in things:
        assert key not in repr(thing)
        assert key not in str(thing)


# --- Settings ---------------------------------------------------------------------------


@pytest.mark.req("FR-11.7", "SEC-6")
@pytest.mark.wp("P4-03")
def test_hosted_speech_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted speech provider's endpoint, model, voice and key come from
    `SPEECH__HOSTED_*`; the key is a secret that no dump or repr shows."""
    settings = _settings(monkeypatch, **HOSTED_SPEECH, SPEECH__HOSTED_VOICE="alloy")
    speech = settings.speech
    assert speech.hosted_base_url == HOSTED_URL
    assert speech.hosted_model == "tts-1"
    assert speech.hosted_voice == "alloy"
    assert isinstance(speech.hosted_api_key, SecretStr)
    assert speech.hosted_api_key.get_secret_value() == SPEECH_KEY
    _no_key_in(SPEECH_KEY, [], settings, speech, settings.model_dump(), settings.model_dump_json())


@pytest.mark.req("FR-11.10", "SEC-6")
@pytest.mark.wp("P3-10")
def test_hosted_embedder_key_read_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted embedder's endpoint, model, dimension and key come from
    `EMBEDDINGS__HOSTED_*`; the key is a secret that no dump or repr shows."""
    settings = _settings(monkeypatch, **HOSTED_EMBED)
    embeddings = settings.embeddings
    assert embeddings.hosted_base_url == HOSTED_URL
    assert embeddings.hosted_model == "text-embedding-3-small"
    assert embeddings.hosted_dims == 8
    assert isinstance(embeddings.hosted_api_key, SecretStr)
    assert embeddings.hosted_api_key.get_secret_value() == EMBED_KEY
    _no_key_in(
        EMBED_KEY, [], settings, embeddings, settings.model_dump(), settings.model_dump_json()
    )


@pytest.mark.req("FR-11.7", "FR-11.10")
@pytest.mark.wp("P4-03")
def test_blank_hosted_variables_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose passes `${VAR:-}` as an empty string: a blank hosted variable is unset, not
    an empty key or URL."""
    blank = dict.fromkeys((*HOSTED_SPEECH, *HOSTED_EMBED), "") | {"SPEECH__HOSTED_VOICE": "  "}
    settings = _settings(monkeypatch, **blank)
    assert settings.speech.hosted_base_url is None
    assert settings.speech.hosted_model is None
    assert settings.speech.hosted_voice is None
    assert settings.speech.hosted_api_key is None
    assert settings.embeddings.hosted_base_url is None
    assert settings.embeddings.hosted_model is None
    assert settings.embeddings.hosted_api_key is None


@pytest.mark.req("REL-7", "SEC-6")
@pytest.mark.wp("P4-03")
@pytest.mark.parametrize("variable", ["SPEECH__HOSTED_API_KEY", "EMBEDDINGS__HOSTED_API_KEY"])
def test_preview_refuses_hosted_keys(monkeypatch: pytest.MonkeyPatch, variable: str) -> None:
    """A preview never holds a production secret: a hosted key in its environment is
    refused like the Jev key (`preview_has_production_secret`)."""
    from tumnis.settings import SettingsError  # noqa: PLC0415

    with pytest.raises(SettingsError) as raised:
        _settings(
            monkeypatch,
            DEPLOYMENT_ENV="preview",
            TUMNIS_ADAPTERS="fake",
            **{variable: "preview-test-not-a-key"},
        )
    assert raised.value.code == "preview_has_production_secret"
    assert "preview-test-not-a-key" not in str(raised.value)


# --- Speech slot ------------------------------------------------------------------------


@pytest.mark.req("FR-11.7", "SEC-6")
@pytest.mark.wp("P4-03")
async def test_hosted_speech_client_gets_key_from_settings(
    monkeypatch: pytest.MonkeyPatch, slots: Any
) -> None:
    """With the hosted provider configured from the environment, the Speech slot builds the
    real `HostedTTS` with the key from Settings: it goes out as the bearer token, and no log
    line carries it."""
    fake = importlib.import_module("tumnis.modules.decisions.adapters.speech.fake")
    transport, requests = _recorder(
        lambda _: httpx.Response(
            200, content=fake.fake_wav(), headers={"content-type": "audio/wav"}
        )
    )
    seen = _resolve_with(monkeypatch, "tumnis.modules.decisions.speech_slot", transport)
    settings = _settings(monkeypatch, **HOSTED_SPEECH, TUMNIS_ADAPTERS="real")

    with capture_logs() as logs:
        slots.configure_speech(settings.speech, net_policy=settings.net_policy())
        assert slots.hosted_speech_state() == "configured"
        engine = importlib.import_module("tumnis.modules.decisions.speech_slot")._engines()[
            "hosted"
        ]
        audio = await engine.synthesize(TEXT)

    assert audio == fake.fake_wav()
    assert [name for name, _ in seen] == ["decisions.speech_hosted"]
    assert seen[0][1]["api_key"] == SPEECH_KEY
    assert seen[0][1]["model"] == "tts-1"
    assert [r.headers["authorization"] for r in requests] == [f"Bearer {SPEECH_KEY}"]
    _no_key_in(SPEECH_KEY, logs, engine)


@pytest.mark.req("FR-11.7", "SEC-6")
@pytest.mark.wp("P4-03")
def test_hosted_speech_without_key_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, slots: Any
) -> None:
    """The hosted endpoint set but `SPEECH__HOSTED_API_KEY` unset: the slot reports
    `not_configured`, builds no hosted engine (Piper still serves) and says once, by
    variable name, what is missing. Nothing at all set is just `not_configured`, silently."""
    settings = _settings(
        monkeypatch,
        TUMNIS_ADAPTERS="fake",
        SPEECH__PIPER_URL="http://10.20.0.6:5000",
        SPEECH__HOSTED_BASE_URL=HOSTED_URL,
        SPEECH__HOSTED_MODEL="tts-1",
    )
    with capture_logs() as logs:
        slots.configure_speech(settings.speech, net_policy=settings.net_policy())
        assert slots.hosted_speech_state() == "not_configured"
        engines = importlib.import_module("tumnis.modules.decisions.speech_slot")._engines()
    assert set(engines) == {"piper"}
    missing = [e for e in logs if e["event"] == "decisions.speech_hosted_not_configured"]
    assert len(missing) == 1
    assert missing[0]["missing"] == ["SPEECH__HOSTED_API_KEY"]

    quiet = _settings(monkeypatch, TUMNIS_ADAPTERS="fake")
    with capture_logs() as logs:
        slots.configure_speech(quiet.speech, net_policy=quiet.net_policy())
        assert slots.hosted_speech_state() == "not_configured"
    assert not [e for e in logs if e["event"] == "decisions.speech_hosted_not_configured"]


# --- Embeddings slot --------------------------------------------------------------------


def _embedding_answer(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)["input"]
    data = [{"index": i, "embedding": [0.125] * 8} for i in range(len(sent))]
    return httpx.Response(200, json={"data": data})


@pytest.mark.req("FR-11.10", "SEC-6")
@pytest.mark.wp("P3-10")
async def test_hosted_embedder_client_gets_key_from_settings(
    monkeypatch: pytest.MonkeyPatch, slots: Any
) -> None:
    """With a local vLLM and the hosted embedder configured, the Embeddings slot holds both,
    the local one first (preferred); the real `HostedEmbeddings` is built with the key from
    Settings and sends it as the bearer token, the local one sends none, and no log line
    carries it."""
    transport, requests = _recorder(_embedding_answer)
    seen = _resolve_with(monkeypatch, "tumnis.modules.decisions.embeddings_slot", transport)
    settings = _settings(
        monkeypatch,
        **HOSTED_EMBED,
        TUMNIS_ADAPTERS="real",
        EMBEDDINGS__BASE_URL=LOCAL_URL,
        EMBEDDINGS__DIMS="8",
    )

    with capture_logs() as logs:
        slots.configure_embeddings(settings.embeddings, net_policy=settings.net_policy())
        assert slots.hosted_embeddings_state() == "configured"
        current = importlib.import_module("tumnis.modules.decisions.embeddings_slot")._current()
        assert current is not None
        local, hosted = current.ordered()
        assert (local.hosted, hosted.hosted) == (False, True)
        assert hosted.model == "text-embedding-3-small"
        assert hosted.dims == 8
        vectors = await hosted.embed(["one", "two"])
        await local.embed(["three"])

    assert vectors == [[0.125] * 8, [0.125] * 8]
    by_name = dict(seen)
    assert by_name["decisions.embeddings_hosted"]["api_key"] == EMBED_KEY
    assert by_name["decisions.embeddings_vllm"].get("api_key") is None
    assert requests[0].headers["authorization"] == f"Bearer {EMBED_KEY}"
    assert "authorization" not in requests[1].headers
    _no_key_in(EMBED_KEY, logs, hosted, current)


@pytest.mark.req("FR-11.10", "SEC-6")
@pytest.mark.wp("P3-10")
def test_hosted_embedder_without_key_is_not_configured(
    monkeypatch: pytest.MonkeyPatch, slots: Any
) -> None:
    """The hosted embedder's endpoint set but `EMBEDDINGS__HOSTED_API_KEY` unset: the slot
    reports `not_configured`, holds only the local embedder and names the missing variable
    once. A hosted embedder alone, fully configured, is the slot's only (fake) adapter."""
    settings = _settings(
        monkeypatch,
        TUMNIS_ADAPTERS="fake",
        EMBEDDINGS__BASE_URL=LOCAL_URL,
        EMBEDDINGS__HOSTED_BASE_URL=HOSTED_URL,
        EMBEDDINGS__HOSTED_MODEL="text-embedding-3-small",
    )
    slot = importlib.import_module("tumnis.modules.decisions.embeddings_slot")
    with capture_logs() as logs:
        slots.configure_embeddings(settings.embeddings, net_policy=settings.net_policy())
        assert slots.hosted_embeddings_state() == "not_configured"
        current = slot._current()
    assert current is not None
    assert [a.hosted for a in current.ordered()] == [False]
    missing = [e for e in logs if e["event"] == "decisions.embeddings_hosted_not_configured"]
    assert len(missing) == 1
    assert missing[0]["missing"] == ["EMBEDDINGS__HOSTED_API_KEY"]

    alone = _settings(monkeypatch, TUMNIS_ADAPTERS="fake", **HOSTED_EMBED)
    slots.configure_embeddings(alone.embeddings, net_policy=alone.net_policy())
    assert slots.hosted_embeddings_state() == "configured"
    only = slot._current()
    assert only is not None
    assert [(a.hosted, a.model, a.dims) for a in only.ordered()] == [
        (True, "text-embedding-3-small", 8)
    ]


# --- Never in the database --------------------------------------------------------------


@pytest.mark.req("FR-11.7", "FR-11.10", "SEC-6")
@pytest.mark.wp("P4-03")
@pytest.mark.parametrize("slot", ["speech", "embeddings"])
def test_provider_config_refuses_hosted_keys(slot: str) -> None:
    """The speech and embeddings slots' keys live only in the server's .env: the provider
    config writer refuses one, so nothing can seal it into `provider_configs`. The decisions
    slot (Jev) still takes its key there."""
    decisions: Any = importlib.import_module("tumnis.modules.decisions.api")
    with pytest.raises(ValidationError) as raised:
        decisions.ProviderConfigIn(
            slot=slot, primary="vllm", model_version="m", api_key=SecretStr("db-test-not-a-key")
        )
    assert "db-test-not-a-key" not in str(raised.value)
    assert decisions.ProviderConfigIn(slot=slot, primary="vllm", model_version="m").api_key is None
    jev = decisions.ProviderConfigIn(
        slot="decisions",
        primary="jev",
        model_version=decisions.PINNED_JEV_DEFAULT,
        api_key=SecretStr("jev-test-key"),
    )
    assert jev.api_key is not None
