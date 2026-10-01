"""One error model: RFC 9457 problem+json with a stable `code` (A3, Errors).

Every error the api answers has the shape
`{schema_version, type, title, status, detail?, code, current?}` (the `api/problem` v1
schema, P0-11) and the media type `application/problem+json`: `ProblemError` raised
anywhere, FastAPI's own 404, 405 and request validation errors (code `validation_error`),
`HTTPException`s, `StaleVersion` (409 `stale_version` with the current row), `NotFound`
(404), `UnsupportedSchemaVersion` (422 `unsupported_schema_version`) and anything
unexpected (500 `internal_error`, no detail). Tests assert on `code`.
"""

import re
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any, Final, Literal

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from tumnis.core.schemas import UnsupportedSchemaVersion, VersionedPayload, versioned

PROBLEM_TYPE: Final = "https://tumnis.dev/problems/"
MEDIA_TYPE: Final = "application/problem+json"
_CODE = re.compile(r"^[a-z][a-z0-9_]*$")

# The code an HTTPException without one of its own gets, by status.
STATUS_CODES: Final[Mapping[int, str]] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    406: "not_acceptable",
    409: "conflict",
    413: "body_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


@versioned("api", "problem", 1)
class Problem(VersionedPayload):
    schema_version: Literal[1] = 1
    type: str  # "https://tumnis.dev/problems/<code>"
    title: str
    status: int
    detail: str | None = None
    code: str  # stable snake_case: not_found, stale_version, invalid_timezone, ...
    current: dict[str, Any] | None = None


def problem(
    status: int,
    code: str,
    detail: str | None = None,
    *,
    title: str | None = None,
    current: Mapping[str, Any] | None = None,
) -> Problem:
    return Problem(
        type=PROBLEM_TYPE + code,
        title=title or code.replace("_", " ").capitalize(),
        status=status,
        detail=detail,
        code=code,
        current=jsonable_encoder(dict(current)) if current is not None else None,
    )


class ProblemError(Exception):
    """Raise anywhere in a request to answer with this problem (and these headers)."""

    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        current: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.problem = problem(status, code, detail, title=title, current=current)
        self.headers = dict(headers or {})

    @property
    def status(self) -> int:
        return self.problem.status

    @property
    def code(self) -> str:
        return self.problem.code


def problem_response(p: Problem, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        p.model_dump(exclude_none=True),
        status_code=p.status,
        media_type=MEDIA_TYPE,
        headers=dict(headers or {}),
    )


def problem_bytes(p: Problem) -> bytes:
    """The body of a problem response, for pure ASGI middleware."""
    return bytes(problem_response(p).body)


async def problem_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ProblemError)  # noqa: S101  # registered for ProblemError only
    return problem_response(exc.problem, exc.headers)


def _from_http(status: int, detail: Any) -> Problem:
    """An HTTPException's detail: a snake_case string is its code (`invalid_cursor`,
    `unauthenticated`); a dict may carry `code`, `detail` and `current`."""
    default = STATUS_CODES.get(status, f"http_{status}")
    phrase = HTTPStatus(status).phrase if status in HTTPStatus._value2member_map_ else None
    if isinstance(detail, Mapping):
        code = str(detail.get("code") or default)
        text = detail.get("detail")
        return problem(status, code, str(text) if text else None, current=detail.get("current"))
    if isinstance(detail, str) and _CODE.match(detail):
        return problem(status, detail, detail)
    text = None if detail is None or detail == phrase else str(detail)
    return problem(status, default, text)


class FixedAllow(StarletteHTTPException):
    """A 405 raised by a route that knows its own `Allow` (RFC 9110, 15.5.6); the
    handler keeps it instead of naming every method served at the path."""

    def __init__(self, allow: str) -> None:
        super().__init__(HTTPStatus.METHOD_NOT_ALLOWED, headers={"Allow": allow})


def _allowed_methods(request: Request) -> str | None:
    """Every method any route serves at this path. Starlette's 405 names only the first
    route that matched the path, so a path with GET and PUT on separate routes would
    answer `Allow: GET` (found by the P0-11 fuzzer)."""
    from starlette.routing import compile_path  # noqa: PLC0415

    from tumnis.core.routing import walk_routes  # noqa: PLC0415  # routing imports errors

    methods: set[str] = set()
    for route in walk_routes(request.app):
        if route.path and route.methods and compile_path(route.path)[0].match(request.url.path):
            methods |= route.methods
    return ", ".join(sorted(methods)) or None


async def http_exception_handler(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)  # noqa: S101
    headers = dict(exc.headers or {})
    if (
        exc.status_code == HTTPStatus.METHOD_NOT_ALLOWED
        and not isinstance(exc, FixedAllow)
        and (allow := _allowed_methods(request))
    ):
        headers["Allow"] = allow
    return problem_response(_from_http(exc.status_code, exc.detail), headers)


def _location(loc: Any) -> str:
    return ".".join(str(part) for part in loc) if isinstance(loc, list | tuple) else str(loc)


async def validation_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors = [f"{_location(e.get('loc', ()))}: {e.get('msg', '')}" for e in exc.errors()]
    return problem_response(problem(422, "validation_error", "; ".join(errors)[:2000] or None))


async def stale_version_handler(_request: Request, exc: Exception) -> Response:
    from tumnis.core.versioning import StaleVersion  # noqa: PLC0415  # versioning imports errors

    assert isinstance(exc, StaleVersion)  # noqa: S101
    detail = "The row changed since you read it; `current` holds it now"
    return problem_response(problem(409, "stale_version", detail, current=exc.current))


async def not_found_handler(_request: Request, exc: Exception) -> Response:
    return problem_response(problem(404, "not_found", "Not found"))


async def unsupported_schema_version_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, UnsupportedSchemaVersion)  # noqa: S101
    return problem_response(problem(422, exc.code, str(exc)))


async def internal_error_handler(_request: Request, _exc: Exception) -> Response:
    return problem_response(problem(500, "internal_error"))


def document_problem_media_type(spec: dict[str, Any]) -> dict[str, Any]:
    """Problem answers are `application/problem+json`: move every response documented with
    the Problem model from `application/json` to that media type, so the OpenAPI document
    (and the fuzzer checking it) says what the api sends."""
    ref = "#/components/schemas/Problem"
    for operations in spec.get("paths", {}).values():
        for operation in operations.values():
            for response in operation.get("responses", {}).values():
                content = response.get("content", {})
                body = content.get("application/json")
                if body is not None and body.get("schema", {}).get("$ref") == ref:
                    content[MEDIA_TYPE] = content.pop("application/json")
    return spec


def install_problem_handlers(app: FastAPI) -> None:
    from tumnis.core.versioning import NotFound, StaleVersion  # noqa: PLC0415

    app.add_exception_handler(ProblemError, problem_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_handler)
    app.add_exception_handler(StaleVersion, stale_version_handler)
    app.add_exception_handler(NotFound, not_found_handler)
    app.add_exception_handler(UnsupportedSchemaVersion, unsupported_schema_version_handler)
    app.add_exception_handler(Exception, internal_error_handler)
