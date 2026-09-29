"""GlitchTip through the Sentry SDK (P0-27, REL-5, SEC-6).

SENTRY_DSN unset keeps the SDK off. With a DSN the SDK sends errors only: no PII, no
performance traces (OpenTelemetry does tracing), no request bodies, no local variables.
`before_send` is the last gate for every event: the request body, cookies and credential
headers go, and the log redaction rules (tumnis.core.logging) run over everything else,
so a token or a message body inside an exception message, an extra or a breadcrumb never
leaves the process.
"""

from typing import Any, Final, cast

import sentry_sdk

from tumnis.core.logging import REDACTED, scrub

# Request headers that carry a credential; the rest (User-Agent, Content-Type) help triage.
DROP_HEADERS: Final = frozenset({"authorization", "cookie", "x-csrf-token", "x-api-key"})


def _clean_request(request: dict[str, Any]) -> dict[str, Any]:
    cleaned = {key: value for key, value in request.items() if key not in {"data", "cookies"}}
    headers = cleaned.get("headers")
    if isinstance(headers, dict):
        cleaned["headers"] = scrub(
            {k: v for k, v in headers.items() if str(k).lower() not in DROP_HEADERS}
        )
    if cleaned.get("query_string"):
        cleaned["query_string"] = REDACTED
    return {key: scrub(value) if key != "headers" else value for key, value in cleaned.items()}


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Strip the request body, cookies and credential headers, then redact the event."""
    request = event.get("request")
    cleaned = {key: value for key, value in event.items() if key != "request"}
    out: dict[str, Any] = scrub(cleaned)
    if isinstance(request, dict):
        out["request"] = _clean_request(request)
    return out


def init_sentry(dsn: str | None, *, environment: str, release: str | None = None) -> bool:
    """Start the SDK when a DSN is set; returns whether it started."""
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        send_default_pii=False,
        traces_sample_rate=0,
        max_request_body_size="never",
        include_local_variables=False,
        # Plain dicts in, plain dicts out; the SDK's Event is a TypedDict of the same shape.
        before_send=cast("Any", before_send),
    )
    return True
