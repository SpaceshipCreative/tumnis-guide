"""Event payload types: the (name, version) registry and emit's schema check (P0-07,
ADR-0011). Pure: `emit` must refuse a bad payload before it touches the session."""

from __future__ import annotations

import uuid
from typing import Any

import pytest


class SpySession:
    """Stands in for AsyncSession and records every call; any call is a failure here."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    async def execute(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append(("execute", args))

    def add(self, *args: Any) -> None:
        self.calls.append(("add", args))

    async def flush(self, *args: Any) -> None:
        self.calls.append(("flush", args))


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
async def test_emit_rejects_payload_that_fails_its_schema() -> None:
    """T-P0-07-13
    An unknown event name, or a payload whose field fails its model, raises
    EventSchemaError before any SQL (the session is a spy).
    """
    from datetime import UTC, datetime  # noqa: PLC0415
    from typing import ClassVar, Literal  # noqa: PLC0415

    from tumnis.core.events import EventPayload, TestPingV1  # noqa: PLC0415
    from tumnis.core.outbox import EventSchemaError, emit  # noqa: PLC0415
    from tumnis.core.tenancy import WorkspaceContext, use_workspace  # noqa: PLC0415
    from tumnis.core.types import SYSTEM_ACTOR  # noqa: PLC0415

    class UnregisteredV1(EventPayload):
        event_name: ClassVar[str] = "never.registered"
        schema_version: Literal[1] = 1

    at = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
    spy = SpySession()
    with use_workspace(WorkspaceContext(uuid.uuid4(), SYSTEM_ACTOR)):
        with pytest.raises(EventSchemaError):
            await emit(spy, UnregisteredV1(), occurred_at=at)  # type: ignore[arg-type]
        bad = TestPingV1.model_construct(note=123)  # type: ignore[arg-type]  # note must be a str
        with pytest.raises(EventSchemaError):
            await emit(spy, bad, occurred_at=at)  # type: ignore[arg-type]
    assert spy.calls == []


@pytest.mark.req("ADR-0011")
@pytest.mark.wp("P0-07")
def test_duplicate_event_type_registration_fails() -> None:
    """T-P0-07-14
    Registering ("x.y", 1) twice raises.
    """
    from typing import ClassVar, Literal  # noqa: PLC0415

    from tumnis.core.events import EventPayload, event_type  # noqa: PLC0415

    @event_type("x.y", 1)
    class XyV1(EventPayload):
        event_name: ClassVar[str] = "x.y"
        schema_version: Literal[1] = 1

    class XyAgainV1(EventPayload):
        event_name: ClassVar[str] = "x.y"
        schema_version: Literal[1] = 1

    with pytest.raises(ValueError, match=r"x\.y"):
        event_type("x.y", 1)(XyAgainV1)
    assert XyV1.event_name == "x.y"
