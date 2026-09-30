"""`VllmVision`: the vision model behind the `Vision` port (P1-16, ADR-0007). One page image
in, the page's Markdown out, from a vLLM server through its OpenAI-compatible chat
endpoint ([vLLM multimodal inputs](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)).

Each page is one chat completion: a user message holding the instruction and the page as
an inline `data:image/png;base64,...` image, at temperature 0. The instruction is Docling's
own Markdown prompt for page-reading vision models, so the model answers bare Markdown; a
reply wrapped in a ```markdown fence is unwrapped. The adapter base owns the timeout and
the breaker; the call goes through the SSRF-guarded client (a self-hosted vLLM is on the
LAN, so the deployment mode decides whether its address may be reached). Only the extract
worker builds it (the pipeline's `_vision()`); the log line names the page, the model and
the latency, never the image or the text.
"""

import base64
import re
from dataclasses import replace
from typing import Any, Final, Literal

import httpx
import structlog

from tumnis.core.adapters.base import (
    Adapter,
    AdapterRejected,
    AdapterTimeout,
    AdapterUnavailable,
    CallPolicy,
)
from tumnis.core.adapters.retry import RetryPolicy
from tumnis.core.clock import Clock
from tumnis.core.net import NetPolicy, Resolver, guarded_client, system_resolver

__all__ = ["PROMPT", "VISION_POLICY", "VllmVision", "page_body"]

_log = structlog.get_logger(__name__)
OP: Final = "page_markdown"
PATH: Final = "/v1/chat/completions"
# Docling's prompt for vision models that answer Markdown (docling.datamodel.stage_model_specs).
PROMPT: Final = (
    "Convert this page to markdown. Do not miss any text and only output the bare markdown!"
)
MAX_TOKENS: Final = 8192  # plan (Docling's API VLM example)
# One attempt per page: the vision step runs inside a DBOS step, which retries (P0-09).
VISION_POLICY: Final = CallPolicy(timeout_s=90.0, retry=RetryPolicy(max_attempts=1))
_SERVER_ERROR: Final = 500
_REQUEST_TIMEOUT: Final = 408
_TOO_MANY: Final = 429
_FENCE: Final = re.compile(r"\A```(?:markdown|md)?[ \t]*\n(?P<body>.*?)\n?```\s*\Z", re.DOTALL)


def page_body(image: bytes, *, model: str) -> dict[str, Any]:
    """The chat completion body sent for one page image (the recordings' `request`)."""
    url = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
    return {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        "temperature": 0.0,
        "max_tokens": MAX_TOKENS,
    }


def _markdown(name: str, body: Any) -> str:
    """The first choice's text, unwrapped from a Markdown fence."""
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AdapterRejected(name, OP, "malformed chat completion") from None
    if not isinstance(content, str):
        raise AdapterRejected(name, OP, "malformed chat completion")
    text = content.strip()
    fenced = _FENCE.match(text)
    return fenced.group("body").strip() if fenced else text


def _translate(name: str, response: httpx.Response) -> None:
    """Raise the adapter error for a non-200 answer."""
    status = response.status_code
    if status == _TOO_MANY:
        try:
            retry_after = float(response.headers.get("retry-after", ""))
        except ValueError:
            retry_after = None
        raise AdapterUnavailable(name, OP, "rate limited (429)", retry_after_s=retry_after)
    if status >= _SERVER_ERROR or status == _REQUEST_TIMEOUT:
        raise AdapterUnavailable(name, OP, f"server error ({status})")
    raise AdapterRejected(name, OP, f"rejected ({status})")


class VllmVision(Adapter):
    name = "knowledge.vision"

    def __init__(  # noqa: PLR0913
        self,
        base_url: str,
        model: str,
        *,
        clock: Clock,
        net_policy: NetPolicy,
        resolver: Resolver = system_resolver,
        transport: httpx.AsyncBaseTransport | None = None,  # MockTransport in tests
        policy: CallPolicy = VISION_POLICY,
    ) -> None:
        super().__init__(policy=policy, clock=clock)
        self._clock = clock
        self._model = model
        target = httpx.URL(base_url)
        ports = net_policy.ports if target.port is None else net_policy.ports | {target.port}
        self._url = str(target.copy_with(path=PATH))
        self._client = guarded_client(
            replace(net_policy, ports=ports),
            timeout=policy.timeout_s,
            resolver=resolver,
            inner=transport,
        )

    async def _send(self, body: dict[str, Any]) -> str:
        try:
            response = await self._client.post(self._url, json=body)
        except httpx.TimeoutException:
            raise AdapterTimeout(self.name, OP) from None
        except httpx.TransportError:
            raise AdapterUnavailable(self.name, OP, "connection failed") from None
        if response.status_code != httpx.codes.OK:
            _translate(self.name, response)
        try:
            payload = response.json()
        except ValueError:
            raise AdapterRejected(self.name, OP, "malformed chat completion") from None
        return _markdown(self.name, payload)

    async def page_markdown(self, image: bytes, *, page: int) -> str:
        body = page_body(image, model=self._model)
        started = self._clock.now()
        markdown: str = await self.call(OP, lambda: self._send(body), idempotent=True)
        _log.info(
            "knowledge.vision_page",
            page=page,
            model=self._model,
            chars=len(markdown),
            latency_ms=int((self._clock.now() - started).total_seconds() * 1000),
        )
        return markdown

    async def health(self) -> Literal["ok", "degraded"]:
        return self.health_state()

    async def aclose(self) -> None:
        await self._client.aclose()
