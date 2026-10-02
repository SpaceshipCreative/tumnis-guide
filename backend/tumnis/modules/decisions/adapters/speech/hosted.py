"""`HostedTTS`: an optional hosted speech provider (P4-03, FR-11.7, Data flow rule 6).

It speaks the OpenAI-compatible `POST /v1/audio/speech` with JSON `{"model", "input",
"voice", "response_format": "wav"}` and the provider's key as a bearer token, and answers
the audio bytes ([OpenAI API reference, createSpeech]
(https://github.com/openai/openai-openapi): `model`, `input` and `voice` are required;
`response_format` takes `wav`). `hosted = True`, so `decisions.api.speak` uses it only when
the workspace allowed hosted speech and never for a local-only project.

Off by default. The Speech slot builds it from the server's .env (`SPEECH__HOSTED_BASE_URL`,
`SPEECH__HOSTED_MODEL`, `SPEECH__HOSTED_VOICE`, `SPEECH__HOSTED_API_KEY`; Scott decision 75):
the key comes from Settings, never from the database. Built lazily by the registry; never
imported by the api process (import-linter `api-never-calls-out`). The log line never
carries the text or the key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

import structlog

from tumnis.core.adapters.base import Adapter, CallPolicy, Health
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.net import NetPolicy, Resolver, system_resolver
from tumnis.modules.decisions.adapters.speech.base import OP, TTS_TIMEOUT_S, check_text
from tumnis.modules.decisions.adapters.speech.piper import AudioEndpoint

if TYPE_CHECKING:
    import httpx

    from tumnis.core.clock import Clock

__all__ = ["DEFAULT_VOICE", "SPEECH_PATH", "HostedTTS", "hosted_body"]

_log = structlog.get_logger(__name__)
SPEECH_PATH: Final = "/v1/audio/speech"
DEFAULT_VOICE: Final = "alloy"


def hosted_body(model: str, text: str, voice: str) -> dict[str, Any]:
    """The request body: the recordings' `request`."""
    return {"model": model, "input": text, "voice": voice, "response_format": "wav"}


class HostedTTS(Adapter):
    name = "decisions.speech_hosted"
    hosted: bool = True
    timeout_s: float = TTS_TIMEOUT_S

    def __init__(  # the endpoint, the model, the key and the injected services
        self,
        base_url: str,
        *,
        model: str,
        api_key: str,
        clock: Clock,
        net_policy: NetPolicy,
        voice: str = DEFAULT_VOICE,
        timeout_s: float = TTS_TIMEOUT_S,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
    ) -> None:
        super().__init__(
            policy=CallPolicy(timeout_s=timeout_s, retry=RetryPolicy(max_attempts=1)),
            clock=clock,
        )
        self._clock = clock
        self.timeout_s = timeout_s
        self.model = model
        self.voice = voice
        root = base_url.rstrip("/").removesuffix("/v1")
        self._endpoint = AudioEndpoint(
            root + SPEECH_PATH,
            adapter=self.name,
            net_policy=net_policy,
            timeout_s=timeout_s,
            resolver=resolver,
            transport=transport,
            bearer=api_key,
        )

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        check_text(text, adapter=self.name)
        body = hosted_body(self.model, text, voice or self.voice)

        async def send() -> bytes:
            return await self._endpoint.post(body, timeout_s=self.timeout_s)

        started = self._clock.now()
        audio = await self.call(OP, send, idempotent=True)
        _log.info(
            "decisions.speech_call",
            provider="hosted",
            model=self.model,
            chars=len(text),
            audio_bytes=len(audio),
            latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
        )
        return audio

    async def health(self) -> Health:
        return self.health_state()

    async def aclose(self) -> None:
        await self._endpoint.aclose()
