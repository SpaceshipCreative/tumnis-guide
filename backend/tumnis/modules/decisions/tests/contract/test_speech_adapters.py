"""The Speech slot's adapters (P4-03, FR-11.7): the contract classes the adapter registry's
sweep (T-P0-09-13) counts, run against each fake and against `PiperTTS` / `HostedTTS`
replaying the recorded exchanges in `tests/recordings/speech/` (no socket is opened), plus
what each sends on the wire and how answers become adapter errors."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from tumnis.core.adapters.contract import AdapterContract
from tumnis.core.adapters.errors import AdapterRejected, AdapterUnavailable
from tumnis.modules.decisions.adapters.speech.base import MAX_SPOKEN_CHARS, SpeechTTS, is_wav

pytestmark = pytest.mark.contract

RECORDINGS = Path(__file__).resolve().parents[1] / "recordings" / "speech"
PIPER_URL = "http://10.20.0.6:5000"  # the agent server's Piper (private, port 5000)
HOSTED_URL = "http://10.20.0.7:8080"  # an OpenAI-compatible speech server in the test
TEXT = "Time for Write proposal. First step: open the proposal outline."
TEST_KEY = "test-key-not-real"


def recording(name: str) -> dict[str, Any]:
    rec: dict[str, Any] = json.loads((RECORDINGS / f"{name}.json").read_text())
    return rec


def replay(name: str, seen: list[httpx.Request] | None = None) -> httpx.MockTransport:
    """Answers the recorded request (same path and JSON body) with the recorded WAV; any
    other request fails the test."""
    rec = recording(name)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        assert request.method == rec["request"]["method"]
        assert request.url.path == rec["request"]["path"]
        assert json.loads(request.content) == rec["request"]["body"]
        answer = rec["response"]
        return httpx.Response(
            answer["status"],
            content=base64.b64decode(answer["body_base64"]),
            headers={"content-type": answer["content_type"]},
        )

    return httpx.MockTransport(handler)


def piper(transport: httpx.AsyncBaseTransport, **extra: Any) -> Any:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.speech.piper import PiperTTS  # noqa: PLC0415

    return PiperTTS(
        PIPER_URL,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        transport=transport,
        **extra,
    )


def hosted(transport: httpx.AsyncBaseTransport, **extra: Any) -> Any:
    from tumnis.core.clock import FixedClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415
    from tumnis.modules.decisions.adapters.speech.hosted import HostedTTS  # noqa: PLC0415

    return HostedTTS(
        HOSTED_URL,
        model="tts-1",
        api_key=TEST_KEY,
        clock=FixedClock(datetime(2026, 3, 9, 12, 0, tzinfo=UTC)),
        net_policy=NetPolicy(mode="self-hosted"),
        transport=transport,
        **extra,
    )


class SpeechContract(AdapterContract[SpeechTTS]):
    port = SpeechTTS

    async def test_answers_a_wav(self, subject: SpeechTTS) -> None:
        audio = await subject.synthesize(TEXT)
        assert is_wav(audio)
        assert await subject.synthesize(TEXT) == audio

    async def test_refuses_text_over_the_limit_or_empty(self, subject: SpeechTTS) -> None:
        with pytest.raises(AdapterRejected):
            await subject.synthesize("x" * (MAX_SPOKEN_CHARS + 1))
        with pytest.raises(AdapterRejected):
            await subject.synthesize("   ")

    async def test_health_is_ok(self, subject: SpeechTTS) -> None:
        assert await subject.health() == "ok"


@pytest.mark.contract
class TestPiperFake(SpeechContract):
    impl = "fake"
    adapter_name = "decisions.speech_piper"

    @pytest.fixture
    def subject(self) -> SpeechTTS:
        from tumnis.modules.decisions.adapters.speech.fake import FakeTTS  # noqa: PLC0415

        fake = FakeTTS()
        assert fake.hosted is False
        return fake


@pytest.mark.contract
class TestPiperRecorded(SpeechContract):
    impl = "recorded"
    adapter_name = "decisions.speech_piper"

    @pytest.fixture
    def subject(self) -> SpeechTTS:
        engine: SpeechTTS = piper(replay("piper"))
        assert engine.hosted is False
        return engine


@pytest.mark.contract
class TestHostedFake(SpeechContract):
    impl = "fake"
    adapter_name = "decisions.speech_hosted"

    @pytest.fixture
    def subject(self) -> SpeechTTS:
        from tumnis.modules.decisions.adapters.speech.fake import FakeHostedTTS  # noqa: PLC0415

        fake = FakeHostedTTS()
        assert fake.hosted is True
        return fake


@pytest.mark.contract
class TestHostedRecorded(SpeechContract):
    impl = "recorded"
    adapter_name = "decisions.speech_hosted"

    @pytest.fixture
    def subject(self) -> SpeechTTS:
        engine: SpeechTTS = hosted(replay("hosted"))
        assert engine.hosted is True
        return engine


@pytest.mark.req("FR-11.7")
@pytest.mark.wp("P4-03")
async def test_piper_sends_text_and_voice_without_a_key() -> None:
    """Piper gets `{"text", "voice"}` at /synthesize with no Authorization header; the hosted
    provider gets its key as a bearer token and asks for WAV."""
    seen: list[httpx.Request] = []
    wav = base64.b64decode(recording("piper")["response"]["body_base64"])

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=wav)

    await piper(httpx.MockTransport(handler), voice="en_US-lessac-medium").synthesize(TEXT)
    await hosted(httpx.MockTransport(handler)).synthesize(TEXT, voice="nova")
    assert [r.url.path for r in seen] == ["/synthesize", "/v1/audio/speech"]
    assert json.loads(seen[0].content) == {"text": TEXT, "voice": "en_US-lessac-medium"}
    assert seen[0].headers.get("authorization") is None
    assert json.loads(seen[1].content) == {
        "model": "tts-1",
        "input": TEXT,
        "voice": "nova",
        "response_format": "wav",
    }
    assert seen[1].headers["authorization"] == f"Bearer {TEST_KEY}"


@pytest.mark.req("FR-11.7")
@pytest.mark.wp("P4-03")
@pytest.mark.parametrize(
    ("status", "content", "error"),
    [
        (503, b"", AdapterUnavailable),
        (429, b"", AdapterUnavailable),
        (400, b"{}", AdapterRejected),
        (200, b"not audio", AdapterRejected),
    ],
    ids=["server_error", "rate_limited", "rejected", "not_a_wav"],
)
async def test_answers_become_adapter_errors(
    status: int, content: bytes, error: type[Exception]
) -> None:
    """5xx and 429 are AdapterUnavailable; another 4xx or a body that is not a WAV is
    AdapterRejected. One attempt only: a late clip is no use."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, content=content)

    with pytest.raises(error):
        await piper(httpx.MockTransport(handler)).synthesize(TEXT)
    assert len(calls) == 1


@pytest.mark.req("FR-11.7")
@pytest.mark.wp("P4-03")
async def test_too_long_text_is_never_sent() -> None:
    """Text over `MAX_SPOKEN_CHARS` is refused before any request."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(500)

    with pytest.raises(AdapterRejected):
        await piper(httpx.MockTransport(handler)).synthesize("x" * (MAX_SPOKEN_CHARS + 1))
    assert calls == []
