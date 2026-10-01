"""The Speech slot's port (P4-03, FR-11.7): `SpeechTTS` turns a focus message's text into a
WAV clip. Callers depend on this protocol only; `decisions.api.speak` picks the engine
(Piper by default, a hosted provider only when allowed and never for a local-only project).

Every engine refuses text longer than `MAX_SPOKEN_CHARS` before sending anything
(`AdapterRejected`) and gives up after its `timeout_s` (`AdapterTimeout`): a clip that
arrives late is no use to a message already on screen, and the PWA then speaks it with the
browser's own voice.
"""

from __future__ import annotations

import io
import wave
from typing import Final, Protocol

from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.adapters.registry import Health

__all__ = [
    "CLIP_TTL_MIN",
    "MAX_SPOKEN_CHARS",
    "TTS_TIMEOUT_S",
    "WAV_MIME",
    "SpeechTTS",
    "check_text",
    "is_wav",
]

MAX_SPOKEN_CHARS: Final = 1_000  # plan default
TTS_TIMEOUT_S: Final = 5.0  # plan default
CLIP_TTL_MIN: Final = 60  # plan default
WAV_MIME: Final = "audio/wav"
OP: Final = "synthesize"


class SpeechTTS(Protocol):
    name: str  # registry name, e.g. "decisions.speech_piper"
    hosted: bool  # True: the text leaves the deployment (Data flow rule 6)
    timeout_s: float

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """The text spoken as a WAV file. AdapterRejected for empty text or text over
        `MAX_SPOKEN_CHARS` (nothing sent) and for an answer that is not a WAV."""
        ...

    async def health(self) -> Health: ...


def check_text(text: str, *, adapter: str) -> None:
    """AdapterRejected unless `text` has something to say within `MAX_SPOKEN_CHARS`."""
    if not text.strip():
        raise AdapterRejected(adapter, OP, "nothing to say")
    if len(text) > MAX_SPOKEN_CHARS:
        raise AdapterRejected(adapter, OP, f"text over {MAX_SPOKEN_CHARS} characters")


def is_wav(audio: bytes) -> bool:
    """A RIFF/WAVE file the standard library can read, with at least one frame."""
    if audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        return False
    try:
        with wave.open(io.BytesIO(audio)) as wav:
            frames: int = wav.getnframes()
    except (wave.Error, EOFError):
        return False
    return frames > 0
