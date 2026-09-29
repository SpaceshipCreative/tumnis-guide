"""OpenTelemetry for the api and the worker: tracer provider, FastAPI instrumentation, and
the W3C carrier that follows an event from the request through the outbox into every
subscriber (P0-27, REL-5). Stubs until the P0-27 implementation lands."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Literal

from fastapi import FastAPI
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter
from opentelemetry.trace import Span, Tracer


def setup_tracing(
    service: Literal["api", "worker"], exporter: SpanExporter | None = None
) -> TracerProvider:
    raise NotImplementedError


def tracer() -> Tracer:
    raise NotImplementedError


def instrument_app(app: FastAPI) -> None:
    raise NotImplementedError


def current_carrier() -> dict[str, str]:
    raise NotImplementedError


@contextmanager
def span_from_carrier(name: str, carrier: Mapping[str, str], **attrs: str) -> Iterator[Span]:
    raise NotImplementedError
    yield  # pragma: no cover
