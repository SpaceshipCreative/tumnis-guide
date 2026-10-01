"""decisions FastAPI router under /v1/decisions; thin calls into api.py.

Settings > Calibration (P3-08, FR-11.5), session only:
- `GET /v1/decisions/calibration`: every decision point's threshold for the pinned model,
  its recheck flag and the evaluation of its labeled decisions.
- `PUT /v1/decisions/thresholds/{point}` `{threshold, reason}`: a human sets a threshold
  with a reason (422 `validation_error` without one, 422 `invalid_threshold` when the
  shape does not fit the point); kept in `thresholds_history` and audited as
  `threshold.changed` in the same transaction.

Speech (P4-03, FR-11.7, SEC-10), session only, on `root_router` under /v1/speech:
- `GET /v1/speech/clips/{id}`: a spoken focus message's WAV clip, `nosniff` and not
  cached, for `CLIP_TTL_MIN` minutes; 404 after that or from another workspace.
"""

from typing import Annotated, Any, Final
from uuid import UUID

from fastapi import Depends, Request, Response

from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.idempotency import SessionDep
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext
from tumnis.modules.decisions import api

router = v1_router("decisions", prefixed=True, tags=["decisions"])
# P4-03: spoken focus messages' clips, under /v1/speech (outside the module's prefix).
root_router = v1_router("decisions", prefix="/speech", tags=["speech"])
CLIP_SCHEMA: Final[dict[str, Any]] = {"type": "string", "format": "binary"}

Session = Annotated[WorkspaceContext, Depends(require_session)]


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.get("/calibration")
@route_policy(RoutePolicy(auth="session"))
async def get_calibration(
    request: Request, ctx: Session, session: SessionDep
) -> api.CalibrationOut:
    """Per decision point: the threshold in force, the recheck flag, and accuracy once 100
    labeled outcomes exist (until then, how many more are needed)."""
    return await api.calibration(ctx, now=_clock(request).now(), session=session)


@root_router.get(
    "/clips/{speech_clip_id}",
    response_class=Response,
    responses={200: {"content": {"audio/wav": {"schema": CLIP_SCHEMA}}}},
)
@route_policy(RoutePolicy(auth="session"))
async def get_clip(speech_clip_id: UUID, request: Request, ctx: Session) -> Response:
    """A spoken focus message's clip (P4-03): `audio/wav` with `nosniff`, while it lives
    (`CLIP_TTL_MIN`); 404 once expired or in another workspace."""
    clip = await api.get_clip(ctx, speech_clip_id, _clock(request).now())
    return Response(
        content=clip.audio,
        media_type=clip.mime,
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"},
    )


@router.put("/thresholds/{point}")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def edit_threshold(
    point: api.DecisionPoint,
    body: api.ThresholdEdit,
    request: Request,
    ctx: Session,
    session: SessionDep,
) -> api.ThresholdOut:
    """Set the point's threshold with a reason; it clears the recheck flag."""
    return await api.edit_threshold(ctx, point, body, now=_clock(request).now(), session=session)
