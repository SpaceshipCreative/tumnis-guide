"""The fake runner's test hooks (P2-04, R-37): `POST /v1/test/fakes/runner/script` takes the
phase 1 body (a recorded answer per profile and skill) and the phase 2 body (the steps of
each run of a task, by title); `GET /v1/test/fakes/runner/last-packet` answers the last
`run` packet the fake received and how many it received since the reset."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import httpx

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

SCRIPT = "/v1/test/fakes/runner/script"
LAST_PACKET = "/v1/test/fakes/runner/last-packet"
PHASE_1 = {"profile": "tumnis-master", "skill": "plan", "result": "plan__x.result.json"}
RESULT = {"outcome": "done", "summary": "Fixed the footer link target"}


@pytest.fixture
def stored_scripts() -> Iterator[None]:
    """The api's lifespan enables the store with fakes; the in-process client skips it."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    fake_scripts.enable()
    try:
        yield
    finally:
        fake_scripts.disable()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_runner_script_takes_both_shapes(
    client: httpx.AsyncClient, stored_scripts: None
) -> None:
    """Both bodies are stored (204) under their keys; a question step (P2-05) is 422."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    assert (await client.post(SCRIPT, json=PHASE_1)).status_code == 204
    task = {"task_title": "Fix footer link", "runs": [[{"result": RESULT}]]}
    assert (await client.post(SCRIPT, json=task)).status_code == 204
    refused = await client.post(
        SCRIPT, json={"task_title": "Fix footer link", "runs": [[{"ask_human": {"prompt": "?"}}]]}
    )
    assert refused.status_code == 422, refused.text
    assert refused.json()["code"] == "invalid_fake_script"

    phase_1 = await fake_scripts.lookup("runner", "tumnis-master/plan")
    assert phase_1 is not None
    assert phase_1["result"] == "plan__x.result.json"
    stored = await fake_scripts.lookup("runner", "task:Fix footer link")
    assert stored is not None
    assert stored["runs"][0][0]["result"]["summary"] == RESULT["summary"]


@pytest.mark.req("REL-7")
@pytest.mark.wp("P2-04")
@pytest.mark.xfail(strict=True, reason="spec:P2-04")
async def test_last_packet_counts_run_messages_and_reset_clears_it(
    client: httpx.AsyncClient, stored_scripts: None
) -> None:
    """Before any dispatch: no packet and zero messages (200, so a poll can wait for the
    first). Each packet the fake records counts once and the last one is answered;
    `POST /v1/test/reset` starts the count again."""
    from tumnis.core import fake_scripts  # noqa: PLC0415

    empty = await client.get(LAST_PACKET)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"packet": None, "run_messages": 0}

    await fake_scripts.record_run_packet({"run_id": "first", "callback": None})
    await fake_scripts.record_run_packet({"run_id": "second", "callback": None})
    seen = (await client.get(LAST_PACKET)).json()
    assert seen == {"packet": {"run_id": "second", "callback": None}, "run_messages": 2}

    assert (await client.post("/v1/test/reset")).status_code == 204
    assert (await client.get(LAST_PACKET)).json() == {"packet": None, "run_messages": 0}
