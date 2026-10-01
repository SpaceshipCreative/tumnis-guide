"""`FakeTTS`: the Speech slot's fake (P4-03). Implements the port directly.

It answers every text with the same fixed 0.2-second WAV (16 kHz, mono, 16-bit silence),
refuses text as the real engines do, and keeps the contract's timeout: `script(delay_s=...)`
makes it slower, and a delay past `timeout_s` raises AdapterTimeout. `script(fail=...)`
raises that error instead. `calls` lists every text it was asked to speak.
"""

from __future__ import annotations

import asyncio
import functools
import io
import wave

from tumnis.core.adapters.errors import AdapterError, AdapterTimeout
from tumnis.core.adapters.registry import Health
from tumnis.modules.decisions.adapters.speech.base import OP, TTS_TIMEOUT_S, check_text

__all__ = ["FAKE_WAV_SECONDS", "FakeHostedTTS", "FakeTTS", "fake_wav"]

FAKE_WAV_SECONDS = 0.2
_RATE = 16_000


@functools.cache
def fake_wav() -> bytes:
    """The fake's fixed clip: 0.2 seconds of 16 kHz mono 16-bit silence."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(_RATE)
        wav.writeframes(b"\x00\x00" * int(_RATE * FAKE_WAV_SECONDS))
    return out.getvalue()


class FakeTTS:
    name: str = "decisions.speech_piper"
    hosted: bool = False
    timeout_s: float = TTS_TIMEOUT_S

    def __init__(
        self, *, hosted: bool = False, timeout_s: float = TTS_TIMEOUT_S, name: str | None = None
    ) -> None:
        self.hosted = hosted
        self.timeout_s = timeout_s
        if name is not None:
            self.name = name
        self.calls: list[str] = []
        self._delay_s = 0.0
        self._fail: AdapterError | None = None

    def script(self, *, delay_s: float = 0.0, fail: AdapterError | None = None) -> None:
        """How the next calls behave: answer after `delay_s`, or raise `fail`."""
        self._delay_s = delay_s
        self._fail = fail

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        check_text(text, adapter=self.name)
        self.calls.append(text)
        if self._fail is not None:
            raise self._fail
        if self._delay_s > self.timeout_s:
            await asyncio.sleep(self.timeout_s)
            raise AdapterTimeout(self.name, OP)
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return fake_wav()

    async def health(self) -> Health:
        return "ok"


class FakeHostedTTS(FakeTTS):
    """The hosted provider's fake: `hosted=True`."""

    name: str = "decisions.speech_hosted"

    def __init__(self, *, timeout_s: float = TTS_TIMEOUT_S) -> None:
        super().__init__(hosted=True, timeout_s=timeout_s)
