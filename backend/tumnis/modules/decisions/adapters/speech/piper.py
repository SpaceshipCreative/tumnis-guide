"""`PiperTTS`: the Speech slot over a local Piper HTTP server (P4-03, FR-11.7).

Piper's server (`python3 -m piper.http_server -m <voice>`, port 5000 by default) answers
`POST /synthesize` with JSON `{"text", "voice"?}` with a WAV file ([Piper HTTP API]
(https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/API_HTTP.md)); without `voice` it
speaks with the voice it was started with. The endpoint sits on the agent server, so calls
go through the SSRF-guarded client (`tumnis.core.net.guarded_client`; a self-hosted
deployment reaches private addresses, a hosted one needs them on its allow-list) with the
endpoint's own port allowed.

One attempt per call and a `TTS_TIMEOUT_S` ceiling: a late clip is useless and the PWA falls
back to the browser's voice. Errors are the four adapter errors (408, 429 and 5xx:
AdapterUnavailable; other 4xx, a body that is not a WAV, a blocked destination:
AdapterRejected). The api process never imports this file (import-linter
`api-never-calls-out`). The log line names the engine, the text length and the latency;
never the text.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any, Final

import httpx
import structlog

from tumnis.core.adapters.base import Adapter, CallPolicy, Health
from tumnis.core.adapters.errors import AdapterRejected, AdapterTimeout, AdapterUnavailable
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.net import SCHEME_PORTS, NetPolicy, Resolver, guarded_client, system_resolver
from tumnis.modules.decisions.adapters.speech.base import OP, TTS_TIMEOUT_S, check_text, is_wav

if TYPE_CHECKING:
    from tumnis.core.clock import Clock

__all__ = ["SYNTHESIZE_PATH", "AudioEndpoint", "PiperTTS", "piper_body"]

_log = structlog.get_logger(__name__)
SYNTHESIZE_PATH: Final = "/synthesize"
_TOO_MANY: Final = 429
_REQUEST_TIMEOUT: Final = 408
_SERVER_ERROR: Final = 500
_CLIENT_ERROR: Final = 400


def piper_body(text: str, voice: str | None) -> dict[str, Any]:
    """The request body: the recordings' `request`."""
    return {"text": text} if voice is None else {"text": text, "voice": voice}


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers["retry-after"])
    except (KeyError, ValueError):
        return None


class AudioEndpoint:
    """One speech endpoint: `post(body)` sends JSON and returns the WAV it answers, or raises
    AdapterTimeout, AdapterUnavailable (connection, 408, 429, 5xx) or AdapterRejected (other
    4xx, an answer that is not a WAV, a blocked destination: SsrfBlocked)."""

    def __init__(  # the endpoint and the injected services
        self,
        url: str,
        *,
        adapter: str,
        net_policy: NetPolicy,
        timeout_s: float,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        bearer: str | None = None,
    ) -> None:
        self.url = httpx.URL(url)
        self._adapter = adapter
        self._headers = {} if bearer is None else {"Authorization": f"Bearer {bearer}"}
        port = self.url.port or SCHEME_PORTS.get(self.url.scheme, 443)
        policy = replace(net_policy, ports=net_policy.ports | {port})
        self._client = guarded_client(policy, timeout=timeout_s, resolver=resolver, inner=transport)

    async def post(self, body: dict[str, Any], *, timeout_s: float) -> bytes:
        try:
            response = await self._client.post(
                self.url, json=body, timeout=timeout_s, headers=self._headers
            )
        except httpx.TimeoutException:
            raise AdapterTimeout(self._adapter, OP) from None
        except httpx.TransportError as exc:
            raise AdapterUnavailable(
                self._adapter, OP, f"connection failed ({type(exc).__name__})"
            ) from None
        status = response.status_code
        if status == _TOO_MANY:
            raise AdapterUnavailable(
                self._adapter, OP, "rate limited (429)", retry_after_s=_retry_after(response)
            )
        if status >= _SERVER_ERROR or status == _REQUEST_TIMEOUT:
            raise AdapterUnavailable(self._adapter, OP, f"server error ({status})")
        if status >= _CLIENT_ERROR:
            raise AdapterRejected(self._adapter, OP, f"rejected ({status})")
        audio = response.content
        if not is_wav(audio):
            raise AdapterRejected(self._adapter, OP, "answer is not a WAV")
        return audio

    async def aclose(self) -> None:
        await self._client.aclose()


class PiperTTS(Adapter):
    name = "decisions.speech_piper"
    hosted: bool = False
    timeout_s: float = TTS_TIMEOUT_S

    def __init__(  # the endpoint, the voice, and the injected services
        self,
        base_url: str,
        *,
        clock: Clock,
        net_policy: NetPolicy,
        voice: str | None = None,
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
        self.voice = voice
        self._endpoint = AudioEndpoint(
            base_url.rstrip("/") + SYNTHESIZE_PATH,
            adapter=self.name,
            net_policy=net_policy,
            timeout_s=timeout_s,
            resolver=resolver,
            transport=transport,
        )

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        check_text(text, adapter=self.name)
        body = piper_body(text, voice or self.voice)

        async def send() -> bytes:
            return await self._endpoint.post(body, timeout_s=self.timeout_s)

        started = self._clock.now()
        audio = await self.call(OP, send, idempotent=True)
        _log.info(
            "decisions.speech_call",
            provider="piper",
            chars=len(text),
            audio_bytes=len(audio),
            latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
        )
        return audio

    async def health(self) -> Health:
        return self.health_state()

    async def aclose(self) -> None:
        await self._endpoint.aclose()
