"""Settings sections and module switches over HTTP (P0-26, SEC-3, SEC-6).

- `GET /v1/settings/modules`: every registered module with `enabled` (the deployment flag,
  then the workspace's flag) and `required`.
- `PUT /v1/settings/modules` `{module, enabled}`: switches a module for the workspace
  through `core.modules.set_module_enabled` (audited `module.toggled`); 422
  `module_required` for a required module, 404 `not_found` for an unknown one.
- `GET /v1/settings/{section}` and `PUT /v1/settings/{section}`: a section registered with
  `settings_store.register_section`, sealed in `workspace_settings`. Reads leave the secret
  fields out of `values` (`secrets_set` names the ones that hold a value), so a secret is
  write-only. A PUT merges `values` into the stored ones (an omitted secret keeps its
  value), validates the result with the section's model (422 `validation_error`), writes
  at `version` (null: must not exist yet; stale: 409 `stale_version`) and records
  `settings.changed` with the section and the field names, never the values.

All `auth="session"`; writes are idempotent and run in the request's transaction, audit
row included. `GET/PUT /v1/settings/workspace` is the auth module's (R-14), mounted first.
"""

from typing import Annotated, Any, Final

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tumnis.core import audit
from tumnis.core.audit_router import require_session
from tumnis.core.clock import Clock
from tumnis.core.errors import ProblemError
from tumnis.core.idempotency import SessionDep
from tumnis.core.modules import (
    MODULES,
    REQUIRED_MODULES,
    ModuleRequired,
    enabled,
    set_module_enabled,
)
from tumnis.core.routing import RoutePolicy, route_policy, v1_router
from tumnis.core.settings_store import SettingSection, get_setting, put_setting, registered_section
from tumnis.core.tenancy import WorkspaceContext
from tumnis.core.versioning import Version

router = v1_router("core", prefix="/settings", tags=["settings"])

Session = Annotated[WorkspaceContext, Depends(require_session)]
CHANGED: Final = "settings.changed"
_MAX_FIELDS: Final = 50


class ModuleFlagOut(BaseModel):
    module: str
    enabled: bool
    required: bool


class ModuleFlagsOut(BaseModel):
    items: list[ModuleFlagOut]


class ModuleFlagIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    module: str = Field(max_length=64)
    enabled: bool


class SettingSectionOut(BaseModel):
    section: str
    values: dict[str, Any]  # the section's fields but its secrets
    secrets_set: list[str]  # the secret fields that hold a value
    version: int | None  # null: never set


class SettingSectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, Any] = Field(max_length=_MAX_FIELDS)
    version: Version | None = None  # null: the section must not exist yet


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _not_found() -> ProblemError:
    return ProblemError(404, "not_found", "Not found")


@router.get("/modules")
@route_policy(RoutePolicy(auth="session"))
async def list_modules(ctx: Session) -> ModuleFlagsOut:
    """Every module, whether it is on for this workspace, and whether it can be off."""
    items = [
        ModuleFlagOut(
            module=module,
            enabled=await enabled(module, ctx.workspace_id),
            required=module in REQUIRED_MODULES,
        )
        for module in MODULES
    ]
    return ModuleFlagsOut(items=items)


@router.put("/modules")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def set_module(
    body: ModuleFlagIn, request: Request, ctx: Session, session: SessionDep
) -> ModuleFlagOut:
    """Switches the module on or off for this workspace. 422 `module_required`."""
    module = body.module
    if module not in MODULES:
        raise _not_found()
    try:
        await set_module_enabled(ctx, module, body.enabled, clock=_clock(request), session=session)
    except ModuleRequired as required:
        raise ProblemError(422, "module_required", str(required)) from None
    return ModuleFlagOut(module=module, enabled=body.enabled, required=module in REQUIRED_MODULES)


def _section(name: str) -> SettingSection:
    found = registered_section(name)
    if found is None:
        raise _not_found()
    return found


def _out(
    section: SettingSection, value: BaseModel | None, version: int | None
) -> SettingSectionOut:
    if value is None:
        return SettingSectionOut(section=section.name, values={}, secrets_set=[], version=None)
    secrets = section.secret_fields
    return SettingSectionOut(
        section=section.name,
        values=value.model_dump(mode="json", exclude=set(secrets)),
        secrets_set=sorted(f for f in secrets if getattr(value, f) not in (None, "")),
        version=version,
    )


def _invalid(exc: ValidationError) -> ProblemError:
    # Field locations and messages only: an error's `input` could be a secret.
    errors = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
    return ProblemError(422, "validation_error", "; ".join(errors)[:2000])


@router.get("/{section}")
@route_policy(RoutePolicy(auth="session"))
async def get_section(section: str, ctx: Session) -> SettingSectionOut:
    """The section's values without its secrets; 404 `not_found` for an unknown section."""
    chosen = _section(section)
    stored = await get_setting(ctx, chosen.name, chosen.model)
    if stored is None:
        return _out(chosen, None, None)
    return _out(chosen, stored.value, stored.version)


@router.put("/{section}")
@route_policy(RoutePolicy(auth="session", idempotent=True))
async def put_section(
    section: str, body: SettingSectionIn, request: Request, ctx: Session, session: SessionDep
) -> SettingSectionOut:
    """Merges `values` into the section at `version`; 409 `stale_version`, 422
    `validation_error`, 404 `not_found`."""
    chosen = _section(section)
    stored = await get_setting(ctx, chosen.name, chosen.model)
    merged = {**(stored.value.model_dump() if stored else {}), **body.values}
    try:
        value = chosen.model.model_validate(merged)
    except ValidationError as exc:
        raise _invalid(exc) from None
    version = await put_setting(
        ctx, chosen.name, value, expected_version=body.version, session=session
    )
    await audit.record(
        session,
        CHANGED,
        details={"section": chosen.name, "fields": sorted(body.values)},
        occurred_at=_clock(request).now(),
    )
    return _out(chosen, value, version)
