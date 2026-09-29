"""The live socket (P0-22, ADR-0004): committed changes reach the browsers of their own
workspace as `{entity, id}` and nothing else; the handshake checks Origin and the session.
Writes go through the probe route in `_live.py` until the tasks module marks its rows."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._pg import DbUrls

pytestmark = [
    pytest.mark.integration,
    pytest.mark.enable_socket,
    # Starlette's TestClient (the WebSocket client) still runs on httpx and says so.
    pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient`"),
]


@pytest.mark.req("ADR-0004")
@pytest.mark.wp("P0-22")
def test_changes_reach_only_their_workspace(db: DbUrls, live_app: FastAPI) -> None:
    """T-P0-22-13
    Sockets open for workspaces A and B; a task created in A reaches A within 1 s as
    exactly `{"entity": "task", "id": ...}`, and B hears nothing of it: after 1 s, B's
    first frame is its own task.
    """
    from starlette.testclient import TestClient  # noqa: PLC0415

    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415
    from tumnis.core.tests.integration._live import (  # noqa: PLC0415
        ORIGIN,
        receive_within,
        wait_until_listening,
    )

    who_a = principal_header(make_workspace(db, "A"), uuid.uuid4())
    who_b = principal_header(make_workspace(db, "B"), uuid.uuid4())

    def create(client: TestClient, who: dict[str, str], title: str) -> str:
        headers = {**who, "Idempotency-Key": f"live-{uuid.uuid4()}"}
        response = client.post("/v1/live-probe/tasks", json={"title": title}, headers=headers)
        assert response.status_code == 201, response.text
        return str(response.json()["id"])

    with TestClient(live_app) as client:
        wait_until_listening(live_app)
        with (
            client.websocket_connect("/ws", headers={**who_a, "Origin": ORIGIN}) as socket_a,
            client.websocket_connect("/ws", headers={**who_b, "Origin": ORIGIN}) as socket_b,
        ):
            task_a = create(client, who_a, "secret title in A")
            assert receive_within(socket_a, 1.0) == {"entity": "task", "id": task_a}

            time.sleep(1.0)
            task_b = create(client, who_b, "in B")
            assert receive_within(socket_b, 1.0) == {"entity": "task", "id": task_b}


@pytest.mark.req("ADR-0004", "SEC-1")
@pytest.mark.wp("P0-22")
def test_socket_rejects_bad_origin_and_no_session(db: DbUrls, live_app: FastAPI) -> None:
    """T-P0-22-14
    A handshake from another origin, without an Origin, or without a session is closed
    with 1008 (policy violation) before it is accepted; the right origin with a session
    connects.
    """
    from starlette.testclient import TestClient  # noqa: PLC0415
    from starlette.websockets import WebSocketDisconnect  # noqa: PLC0415

    from tests.fixtures import make_workspace  # noqa: PLC0415
    from tumnis.core.tests.integration._demo import principal_header  # noqa: PLC0415
    from tumnis.core.tests.integration._live import ORIGIN  # noqa: PLC0415

    who = principal_header(make_workspace(db), uuid.uuid4())
    refused = [
        {**who, "Origin": "https://evil.example"},
        {**who, "Origin": "http://testserver.evil.example"},
        who,  # no Origin
        {"Origin": ORIGIN},  # no session
    ]
    with TestClient(live_app) as client:
        for headers in refused:
            with (
                pytest.raises(WebSocketDisconnect) as closed,
                client.websocket_connect("/ws", headers=headers) as ws,
            ):
                ws.receive_json()
            assert closed.value.code == 1008, headers

        with client.websocket_connect("/ws", headers={**who, "Origin": ORIGIN}) as ws:
            ws.close()
