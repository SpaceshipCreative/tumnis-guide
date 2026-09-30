"""knowledge FastAPI router; thin calls into api.py.

The router takes no prefix: the brief read sits under /v1/projects/{id}/brief (P0-24), and
every other route spells out its /v1/knowledge path.

Storage locations (P1-14, FR-15.7), all `auth="session"` (the Settings screen):
`GET /knowledge/locations` (every location; a workspace has a handful, so a bare list),
`POST /knowledge/locations` (201), `POST /knowledge/locations/{storage_location_id}/test`
(test the connection now; drains queued writes when healthy), `POST
/knowledge/locations/{storage_location_id}/host-key` (P3-14: pin the SFTP server's key
when the fingerprint typed matches the key it shows; a reason when re-pinning), `POST
/knowledge/locations/{storage_location_id}/default` (versioned),
and `PUT /knowledge/projects/{project_id}/folder` (move an empty project folder to another
location). Each handler finds its row (404) before any rule about the body. Storage calls
take the deployment's SSRF policy from the settings.
"""

from pathlib import Path
from typing import Annotated, Any, Final
from urllib.parse import quote
from uuid import UUID

from fastapi import Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, StringConstraints

from tumnis.core.audit_router import require_session
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.ids import uuid7
from tumnis.core.net import NetPolicy
from tumnis.core.principal import principal_of
from tumnis.core.routing import RoutePolicy, authorize, route_policy, v1_router
from tumnis.core.tenancy import WorkspaceContext, tenant_session
from tumnis.core.versioning import Version
from tumnis.modules.knowledge import api, uploads
from tumnis.modules.knowledge.rules import MAX_UPLOAD_BYTES

router = v1_router("knowledge", tags=["knowledge"])

Session = Annotated[WorkspaceContext, Depends(require_session)]


# --- P1-14: storage locations and project folders ---------------------------------------


class DefaultIn(BaseModel):
    version: Version


class FolderIn(BaseModel):
    location_id: UUID


def _net(request: Request) -> NetPolicy:
    policy: NetPolicy = request.app.state.settings.net_policy()
    return policy


@router.get("/knowledge/locations")
@route_policy(
    RoutePolicy(
        auth="session",
        unpaginated_reason="a workspace's storage locations: a handful, set up by hand",
    )
)
async def list_locations(ctx: Session) -> list[api.LocationOut]:
    async with tenant_session(ctx) as s:
        return await api.list_locations(s)


@router.post("/knowledge/locations", status_code=201)
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def create_location(
    body: api.LocationIn, request: Request, _ctx: Session, session: SessionDep
) -> api.LocationOut:
    return await api.create_location(session, body, net=_net(request))


@router.post("/knowledge/locations/{storage_location_id}/test")
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="a connection test must reach the location again, never replay",
    )
)
async def test_location(
    storage_location_id: UUID, request: Request, ctx: Session
) -> api.LocationOut:
    async with tenant_session(ctx) as s:
        return await api.check_location(s, storage_location_id, net=_net(request))


class HostKeyIn(BaseModel):
    sha256: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    reason: Annotated[str, StringConstraints(max_length=500)] | None = None


@router.post("/knowledge/locations/{storage_location_id}/host-key")
@route_policy(
    RoutePolicy(
        auth="session",
        idempotent=False,
        not_idempotent_reason="pinning reads the server's key again, never a replay",
    )
)
async def confirm_host_key(
    storage_location_id: UUID, body: HostKeyIn, request: Request, ctx: Session
) -> api.LocationOut:
    async with tenant_session(ctx) as s:
        return await api.confirm_host_key(
            s, storage_location_id, body.sha256, reason=body.reason, net=_net(request)
        )


@router.post("/knowledge/locations/{storage_location_id}/default")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def set_default_location(
    storage_location_id: UUID, body: DefaultIn, _ctx: Session, session: SessionDep
) -> api.LocationOut:
    return await api.set_default_location(session, storage_location_id, body.version)


@router.put("/knowledge/projects/{project_id}/folder")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def set_project_folder(
    project_id: UUID, body: FolderIn, request: Request, _ctx: Session, session: SessionDep
) -> api.ProjectFolderOut:
    return await api.set_project_location(session, project_id, body.location_id, net=_net(request))


# --- P0-24: the project page's Brief rail -----------------------------------------------
# The brief read sits under /v1/projects/{id}/brief and the text-entry edit under
# /v1/knowledge/documents/{id}. Keep this block separate.


class TextDocumentPatch(BaseModel):
    body_md: Annotated[str, StringConstraints(max_length=100_000)]
    version: Version


@router.get("/projects/{project_id}/brief")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"context:read"}),
        project_param="path:project_id",
    )
)
async def get_brief(project_id: UUID, session: SessionDep) -> api.DocumentDTO:
    """The project's pinned brief (a text entry); 404 until the project's subscriber ran."""
    return await api.get_brief(project_id, session=session)


@router.patch("/knowledge/documents/{document_id}")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"knowledge:write"}),
        idempotent=True,
        project_param="lookup:knowledge",
    )
)
async def update_document(
    document_id: UUID, body: TextDocumentPatch, session: SessionDep
) -> api.DocumentDTO:
    """Replace a text entry's Markdown body; 409 `stale_version` with the current entry."""
    return await api.update_text_document(
        session, document_id, body_md=body.body_md, version=body.version
    )


# --- P1-16: uploads and files --------------------------------------------------------------

MIB = 1024 * 1024
FILE_SCHEMA: Final[dict[str, Any]] = {"type": "string", "format": "binary"}
UPLOAD_POLICY = RoutePolicy(
    auth="session_or_key",
    scopes=frozenset({"knowledge:write"}),
    idempotent=False,
    not_idempotent_reason=(
        "the idempotency layer buffers the body, which an upload streams to disk; "
        "a retry is a new document"
    ),
    # A backstop only: the handler refuses the file itself (413 `too_large`) the moment it
    # passes MAX_UPLOAD_BYTES. The margin is two chunks' worth so that refusal fires first.
    max_body_bytes=MAX_UPLOAD_BYTES + 2 * MIB,
)


def _upload_ctx(request: Request, project_id: UUID | None) -> WorkspaceContext:
    """The caller's workspace context; a project-limited key may only upload to a project
    inside its limit (the project is in the form, so the policy's `project_param` cannot
    name it): anything else is 404, as R-28 has it."""
    principal = principal_of(request)
    authorize(principal, UPLOAD_POLICY, project_id)
    if project_id is None and principal.project_ids is not None:
        raise ProblemError(404, "not_found", "Not found")
    return principal.workspace_context()


# The body is streamed by hand (uploads.spool_upload), so FastAPI cannot infer it: the
# schema is declared through `openapi_extra` (FastAPI, "Custom OpenAPI path operation
# schema"), which gives the generated client a typed multipart body.
UPLOAD_BODY: Final[dict[str, Any]] = {
    "required": True,
    "content": {
        "multipart/form-data": {
            "schema": {
                "type": "object",
                "required": ["file"],
                "properties": {
                    "file": FILE_SCHEMA,
                    "project_id": {"type": "string", "format": "uuid"},
                    "title": {"type": "string"},
                },
            }
        }
    },
}


@router.post("/knowledge/documents", status_code=202, openapi_extra={"requestBody": UPLOAD_BODY})
@route_policy(UPLOAD_POLICY)
async def upload_document(request: Request) -> api.UploadAccepted:
    """Upload a file (multipart: `file`, and optionally `project_id` and `title`). The
    answer is 202 with the document in `pending_scan`: it is scanned, its type read from its
    content and its text extracted by the extract worker. 413 `too_large` past 50 MiB."""
    version_id = uuid7()
    spool = Path(request.app.state.settings.knowledge.spool_dir) / str(version_id)

    async def early(fields: dict[str, str]) -> None:
        project_id = uploads.parse_project_id(fields.get("project_id"))
        await api.check_upload_target(_upload_ctx(request, project_id), project_id)

    body = await uploads.spool_upload(request, spool, on_file=early)
    try:
        project_id = uploads.parse_project_id(body.fields.get("project_id"))
        ctx = _upload_ctx(request, project_id)
        await api.check_upload_target(ctx, project_id)
        accepted = await api.begin_upload(
            ctx,
            project_id=project_id,
            name=body.name or "upload",
            title=body.fields.get("title") or None,
            sha256=body.sha256,
            size=body.size,
            version_id=version_id,
        )
        await api.enqueue_extract(ctx, version_id, "spool")
    except BaseException:
        spool.unlink(missing_ok=True)
        raise
    return accepted


@router.get("/knowledge/documents/{document_id}")
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"context:read"}),
        project_param="lookup:knowledge",
    )
)
async def get_document(document_id: UUID, session: SessionDep) -> api.DocumentStatusOut:
    """A document's state (status, reason, kind, path): what the upload flow polls."""
    return await api.get_document(session, document_id)


@router.get(
    "/files/{document_id}",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/octet-stream": {"schema": FILE_SCHEMA}}}},
)
@route_policy(
    RoutePolicy(
        auth="session_or_key",
        scopes=frozenset({"context:read"}),
        project_param="lookup:knowledge",
    )
)
async def get_file(
    document_id: UUID, request: Request, version: int | None = None
) -> StreamingResponse:
    """The document's original file, always as a download (`attachment`, octet-stream,
    `nosniff`); 409 `not_available` until the document is `ready`. `version` is a version
    number."""
    ctx = principal_of(request).workspace_context()
    net = _net(request)
    info = await api.download_info(ctx, document_id, version_no=version, net=net)
    return StreamingResponse(
        api.stream_file(ctx, info, net=net),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(info.name, safe='')}",
            "Content-Length": str(info.size),
            "X-Content-Type-Options": "nosniff",
        },
    )
