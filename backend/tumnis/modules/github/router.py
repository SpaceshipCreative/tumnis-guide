"""github FastAPI router under /v1/github; thin calls into api.py (P2-13, SEC-5).

`POST /v1/github/webhook/{workspace_id}`: the signed webhook endpoint, built for when a
relay forwards GitHub's deliveries (a private-network Tumnis cannot receive them itself).
GitHub sends no identity but the signature, so the path names the workspace and its
webhook secret (Settings > GitHub) is what authenticates the call: no session, no key, no
CSRF token (it is not a browser). Off (404 `not_found`) while the module is off for the
workspace or no secret is set; 401 `invalid_signature` for a signature that does not match
the raw body (nothing is stored); 409 `duplicate_delivery` for an `X-GitHub-Delivery` seen
before; else 202, with a refresh queued for each tracked pull request the payload names.
The body is capped at 1 MiB (GitHub's own cap is 25 MB; plan default). The route makes no
outbound call.
"""

from typing import Literal
from uuid import UUID

from fastapi import Request
from pydantic import BaseModel

from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.modules.github import api

router = v1_router("github", prefixed=True, tags=["github"])

WEBHOOK = RoutePolicy(
    auth="none",
    idempotent=False,
    not_idempotent_reason="X-GitHub-Delivery is the key; a repeated delivery is answered 409",
    csrf=False,
    csrf_exempt_reason="GitHub's servers call it, not a browser: the HMAC signature is the proof",
    max_body_bytes=1_048_576,
)


class WebhookOut(BaseModel):
    status: Literal["accepted"] = "accepted"


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


@router.post("/webhook/{workspace_id}", status_code=202)
@route_policy(WEBHOOK)
async def webhook(workspace_id: UUID, request: Request) -> WebhookOut:
    outcome = await api.accept_webhook(
        workspace_id,
        body=await request.body(),
        signature=request.headers.get("X-Hub-Signature-256"),
        delivery_id=request.headers.get("X-GitHub-Delivery"),
        event=request.headers.get("X-GitHub-Event"),
        now=_clock(request).now(),
    )
    match outcome:
        case "off":
            raise ProblemError(404, "not_found", "Not found")
        case "bad_signature":
            raise ProblemError(401, "invalid_signature", "The signature does not match the body")
        case "no_delivery_id":
            raise ProblemError(400, "missing_delivery_id", "X-GitHub-Delivery is required")
        case "duplicate":
            raise ProblemError(409, "duplicate_delivery", "This delivery was already accepted")
    return WebhookOut()
