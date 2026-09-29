"""OpenTelemetry for the api and the worker (P0-27, REL-5).

One trace follows a request through the outbox into every subscriber: `emit` stores
`current_carrier()` (the W3C `traceparent`) on the outbox row, the envelope carries it into
the `deliver_event` workflow, and each subscriber runs inside `span_from_carrier`, a child
of the request span. DBOS may replay a subscriber on recovery; both attempts' spans share
the trace, which is what you want when reading a retry.

The process has one SDK tracer provider, set globally on the first `setup_tracing` call
(the FastAPI instrumentation and `tracer()` use the global provider). Its span processor is
swapped, not stacked, by later calls: an explicit exporter (tests: the in-memory one) gets
a synchronous processor; otherwise OTEL_EXPORTER_OTLP_ENDPOINT, when set, gets a batching
OTLP/HTTP exporter; with neither, spans get IDs and go nowhere (no trace store is chosen
yet, architecture open question 4). DBOS's own OTLP tracing stays off (`enable_otlp` unset).
"""

import os
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Final, Literal

from fastapi import FastAPI
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace import Span as SdkSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Span, SpanKind, Tracer

OTLP_ENDPOINT_ENV: Final = "OTEL_EXPORTER_OTLP_ENDPOINT"
INSTRUMENTATION_NAME: Final = "tumnis"
# Probes and scrapes would be most of the spans; they carry no request worth tracing.
EXCLUDED_URLS: Final = "/health/live$,/health/ready$,/metrics$"


class _SwappableProcessor(SpanProcessor):
    """Forwards to the current processor (or nowhere); `swap` replaces it and shuts the old
    one down, flushing what it held."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: SpanProcessor | None = None

    def swap(self, processor: SpanProcessor | None) -> None:
        with self._lock:
            old, self._current = self._current, processor
        if old is not None:
            old.shutdown()

    def on_start(self, span: SdkSpan, parent_context: Context | None = None) -> None:
        current = self._current
        if current is not None:
            current.on_start(span, parent_context=parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        current = self._current
        if current is not None:
            current.on_end(span)

    def shutdown(self) -> None:
        self.swap(None)

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        current = self._current
        return current.force_flush(timeout_millis) if current is not None else True


_processor = _SwappableProcessor()
_provider: TracerProvider | None = None
_setup_lock = threading.Lock()


def setup_tracing(
    service: Literal["api", "worker"], exporter: SpanExporter | None = None
) -> TracerProvider:
    """The process's tracer provider, created and set globally on the first call (its
    resource names the first service); every call picks the exporter anew."""
    global _provider  # noqa: PLW0603  # one provider per process
    with _setup_lock:
        if _provider is None:
            _provider = TracerProvider(
                resource=Resource.create({SERVICE_NAME: f"tumnis-{service}"})
            )
            _provider.add_span_processor(_processor)
            trace.set_tracer_provider(_provider)
        provider = _provider
    if exporter is not None:
        _processor.swap(SimpleSpanProcessor(exporter))
    elif os.environ.get(OTLP_ENDPOINT_ENV):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: PLC0415
            OTLPSpanExporter,  # reads OTEL_EXPORTER_OTLP_* itself
        )

        _processor.swap(BatchSpanProcessor(OTLPSpanExporter()))
    else:
        _processor.swap(None)
    return provider


def tracer() -> Tracer:
    return trace.get_tracer(INSTRUMENTATION_NAME)


def instrument_app(app: FastAPI) -> None:
    """A SERVER span per HTTP request (named by method and route template), continuing an
    incoming `traceparent`; the per-message send and receive spans are left out."""
    FastAPIInstrumentor.instrument_app(
        app, excluded_urls=EXCLUDED_URLS, exclude_spans=["receive", "send"]
    )


def current_carrier() -> dict[str, str]:
    """The current trace context as W3C headers ({"traceparent": "00-…"}); empty when no
    span is recording."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    return carrier


@contextmanager
def span_from_carrier(name: str, carrier: Mapping[str, str], **attrs: str) -> Iterator[Span]:
    """A CONSUMER span, current for the block, whose parent is the span in `carrier` (a new
    trace when the carrier is empty)."""
    ctx = propagate.extract(carrier)
    with tracer().start_as_current_span(
        name, context=ctx, kind=SpanKind.CONSUMER, attributes=attrs
    ) as span:
        yield span
