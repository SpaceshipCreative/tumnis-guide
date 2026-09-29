"""JSON logging: structlog for our code, the same chain for stdlib loggers (uvicorn,
SQLAlchemy, DBOS), trace IDs on every line inside a span, and redaction as the last step
before rendering (P0-27, REL-5; the redaction rules are P0-16's, SEC-6).

`configure_logging()` runs once per process (the CLI's `api` and `worker`): one handler on
the root logger renders every record as one JSON line. uvicorn's loggers propagate to it
(uvicorn starts with `log_config=None`); DBOS's logger gets the same handler directly, so
DBOS never installs its own console format.
"""

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping, Sequence
from typing import IO, Any, Final

import structlog
from opentelemetry import trace

HANDLER_NAME: Final = "tumnis_json"
REDACTED: Final = "[redacted]"

# Dropped wherever they sit: bodies, prompts and message contents never reach a log line.
DROP_KEYS: Final = re.compile(
    r"^(body|body_text|body_html.*|html|prompt|messages|content|text|payload|packet)$", re.I
)
# Kept as a key, value replaced: credentials and anything that holds one.
MASK_KEYS: Final = re.compile(
    r"(token|secret|password|passwd|authorization|cookie|api[_-]?key|hmac|pepper|csrf)", re.I
)
# Replaced inside every string, the event message included.
VALUE_PATTERNS: Final = (
    re.compile(r"\btm[ntd]_[a-z2-7]{12}_[A-Za-z0-9_-]{43}\b"),  # Tumnis keys and tokens
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b"),  # JWT-shaped
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"(?i)\b(api[_-]?key|secret|password|token)=[^\s&]+"),
)
# structlog's own bookkeeping keys pass through untouched (ProcessorFormatter needs them).
_META_KEYS: Final = frozenset({"_record", "_from_structlog"})
# Loggers that configure handlers of their own; they are pointed at ours.
_PROPAGATING: Final = ("uvicorn", "uvicorn.error", "uvicorn.access")
_DBOS_LOGGER: Final = "dbos"


def scrub_text(text: str) -> str:
    for pattern in VALUE_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def scrub(value: Any) -> Any:
    """Recursively: drop DROP_KEYS, mask MASK_KEYS, scrub VALUE_PATTERNS in strings."""
    if isinstance(value, str):
        return scrub_text(value)
    if isinstance(value, Mapping):
        return {
            key: REDACTED if MASK_KEYS.search(str(key)) else scrub(item)
            for key, item in value.items()
            if not DROP_KEYS.match(str(key))
        }
    if isinstance(value, list | tuple | set | frozenset):
        return [scrub(item) for item in value]
    return value


def redact(_: Any, __: str, event_dict: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Recursively: drop DROP_KEYS, mask MASK_KEYS values as '[redacted]', replace
    VALUE_PATTERNS matches in every string (including the event message) with '[redacted]'."""
    meta = {key: event_dict[key] for key in _META_KEYS if key in event_dict}
    rest = {key: value for key, value in event_dict.items() if key not in _META_KEYS}
    event_dict.clear()
    event_dict.update(scrub(rest))
    event_dict.update(meta)
    return event_dict


def add_trace_ids(
    _: Any, __: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict


SHARED: list[structlog.typing.Processor] = [
    structlog.contextvars.merge_contextvars,
    structlog.processors.add_log_level,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.format_exc_info,
    add_trace_ids,
    redact,  # last before render: nothing added after it escapes redaction
]


def _formatter() -> structlog.stdlib.ProcessorFormatter:
    foreign: Sequence[structlog.typing.Processor] = [
        structlog.stdlib.add_logger_name,
        structlog.stdlib.ExtraAdder(),
        *SHARED,
    ]
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=foreign,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )


class _StdoutHandler(logging.StreamHandler[IO[str]]):
    """Writes to whatever `sys.stdout` is at emit time, so a swapped (and later closed)
    stdout is never kept."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self) -> IO[str]:
        return sys.stdout

    @stream.setter
    def stream(self, _value: IO[str]) -> None:
        pass


def configure_logging(*, stream: IO[str] | None = None, level: str = "INFO") -> None:
    """Every log record in the process becomes one JSON line on `stream` (stdout)."""
    handler: logging.StreamHandler[IO[str]] = (
        logging.StreamHandler(stream) if stream is not None else _StdoutHandler()
    )
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(_formatter())

    root = logging.getLogger()
    root.handlers[:] = [h for h in root.handlers if h.get_name() != HANDLER_NAME]
    root.addHandler(handler)
    root.setLevel(level)
    for name in _PROPAGATING:
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    dbos = logging.getLogger(_DBOS_LOGGER)
    dbos.handlers[:] = [handler]
    dbos.propagate = False

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            *SHARED,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
