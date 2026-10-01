"""The Speech slot's TTS contract (P4-03, FR-11.7): every `SpeechTTS` answers a valid,
non-empty WAV, the same bytes for the same text, refuses text over `MAX_SPOKEN_CHARS`
before sending anything, and gives up with `AdapterTimeout` when the engine is slower than
its timeout.

`[fake]` runs everywhere. `[piper]` runs against a local Piper HTTP server
(`python3 -m piper.http_server -m <voice>`, [Piper HTTP API]
(https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md)): the nightly job
starts one and sets `PIPER_URL`; without it the case is skipped
(`pytest -m "contract and slow"` with the engine present).
"""

from __future__ import annotations

import importlib
import io
import os
import wave
from typing import Any

import pytest

pytestmark = pytest.mark.contract

PIPER_URL = os.environ.get("PIPER_URL")
TEXT = "Time for Write proposal. First step: open the proposal outline."
SLOW_TIMEOUT_S = 0.05


def _fake(timeout_s: float, *, slow: bool) -> Any:
    speech = importlib.import_module("tumnis.modules.decisions.adapters.speech.fake")
    fake = speech.FakeTTS(timeout_s=timeout_s)
    if slow:
        fake.script(delay_s=timeout_s * 20)
    return fake


def _piper(timeout_s: float, *, slow: bool) -> Any:
    from tumnis.core.clock import SystemClock  # noqa: PLC0415
    from tumnis.core.net import NetPolicy  # noqa: PLC0415

    piper = importlib.import_module("tumnis.modules.decisions.adapters.speech.piper")

    assert PIPER_URL is not None
    return piper.PiperTTS(
        PIPER_URL,
        clock=SystemClock(),
        net_policy=NetPolicy(mode="self-hosted"),
        timeout_s=0.001 if slow else timeout_s,  # no real engine answers in a millisecond
    )


ENGINES = [
    pytest.param(_fake, id="fake"),
    pytest.param(
        _piper,
        id="piper",
        marks=[
            pytest.mark.slow,
            pytest.mark.skipif(PIPER_URL is None, reason="PIPER_URL is not set (nightly only)"),
        ],
    ),
]


@pytest.mark.req("FR-11.7")
@pytest.mark.wp("P4-03")
@pytest.mark.xfail(strict=True, reason="spec:P4-03")
@pytest.mark.parametrize("make", ENGINES)
async def test_tts_contract(make: Any) -> None:
    """T-P4-03-05 (fake), T-P4-03-06 (piper)
    A valid, non-empty WAV; deterministic; over `MAX_SPOKEN_CHARS` is refused before
    anything is sent; slower than the timeout is AdapterTimeout; health is ok.
    """
    from tumnis.core.adapters.errors import AdapterRejected, AdapterTimeout  # noqa: PLC0415

    base = importlib.import_module("tumnis.modules.decisions.adapters.speech.base")
    max_chars, timeout_s = base.MAX_SPOKEN_CHARS, base.TTS_TIMEOUT_S

    engine = make(timeout_s, slow=False)
    audio = await engine.synthesize(TEXT)
    assert audio[:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"
    with wave.open(io.BytesIO(audio)) as wav:
        assert wav.getnchannels() >= 1
        assert wav.getframerate() > 0
        assert wav.getnframes() > 0
    assert await engine.synthesize(TEXT) == audio

    with pytest.raises(AdapterRejected):
        await engine.synthesize("x" * (max_chars + 1))

    slow = make(SLOW_TIMEOUT_S, slow=True)
    with pytest.raises(AdapterTimeout):
        await slow.synthesize(TEXT)

    assert await engine.health() == "ok"
