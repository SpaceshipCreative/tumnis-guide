"""One error model: RFC 9457 problem+json with a stable `code` (A3, Errors).

P0-08 lands `Problem`, `ProblemError` and its handler (module flags answer 404
`not_found`); P0-10 renders FastAPI's own 404, 405 and validation errors the same way.
"""

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.responses import Response

PROBLEM_TYPE = "https://tumnis.dev/problems/"
MEDIA_TYPE = "application/problem+json"


class Problem(BaseModel):
    type: str  # "https://tumnis.dev/problems/<code>"
    title: str
    status: int
    detail: str | None = None
    code: str  # stable snake_case: not_found, stale_version, invalid_timezone, ...
    current: dict[str, Any] | None = None


class ProblemError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        detail: str | None = None,
        *,
        title: str | None = None,
        current: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or code)
        self.problem = Problem(
            type=PROBLEM_TYPE + code,
            title=title or code.replace("_", " ").capitalize(),
            status=status,
            detail=detail,
            code=code,
            current=current,
        )


async def problem_handler(_request: Request, exc: Exception) -> Response:
    assert isinstance(exc, ProblemError)  # noqa: S101  # registered for ProblemError only
    problem = exc.problem
    return JSONResponse(
        problem.model_dump(exclude_none=True), status_code=problem.status, media_type=MEDIA_TYPE
    )


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, problem_handler)
