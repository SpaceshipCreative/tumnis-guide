"""`POST /v1/test/fakes/{adapter}/script` (R-37, the Ops row of P0-04): a fake scripted
through the api reaches the fakes of every process, because compose.test runs the api and
the worker as separate containers. The script lives in Postgres; `POST /v1/test/reset`
empties it with everything else; the route exists only with fake adapters."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from tests.fixtures import settings_for

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

# The A1.1 scripts, as frontend/e2e/journeys/J2.spec.ts posts them.
JEV_SCRIPT = {
    "question": "quick_add_label",
    "answer": "hybrid",
    "confidence": 0.93,
    "latency_ms": 250,
}
PLACEHOLDER = "Open the invoice template"
GENERATION_SCRIPT = {"first_action": PLACEHOLDER}

# The worker's side, in a process of its own: it enables the store as the worker does,
# builds the fakes through the adapter registry and asks them once each.
CHILD = """
import asyncio, json, sys

import tumnis.wiring  # registers the adapters and their fake-script parsers
from tumnis.core import db, fake_scripts
from tumnis.core.adapters.registry import resolve
from tumnis.modules.decisions.catalog import DecisionPoint, build_request


async def main() -> None:
    db.configure(sys.argv[1], sys.argv[1], pooled=False)
    fake_scripts.enable()
    jev = resolve("decisions.jev", "fake")
    req = build_request(DecisionPoint.QUICK_ADD_LABEL, {"title": "Send Acme the March invoice"})
    answered = await jev.ask(req, model="fake", timeout_ms=800)
    generation = resolve("decisions.vllm_generation", "fake")
    text = await generation.complete(system="s", user="u", max_tokens=50, timeout_ms=1000)
    await db.dispose()
    print(json.dumps({
        "label": answered.answers["label"].model_dump(mode="json"),
        "latency_ms": answered.latency_ms,
        "first_action": text,
    }))


asyncio.run(main())
"""


async def _run_child(app_url: str) -> dict[str, Any]:
    env = {**os.environ, "TUMNIS_ADAPTERS": "fake"}
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        CHILD,
        app_url,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=60)
    assert proc.returncode == 0, err.decode()
    result: dict[str, Any] = json.loads(out.decode().strip().splitlines()[-1])
    return result


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_script_written_through_the_api_reaches_a_fake_in_another_process(
    client: httpx.AsyncClient, db: DbUrls
) -> None:
    """T-P0-04-17
    The A1.1 scripts posted to the api (Jev answers `hybrid` at 0.93 after 250 ms;
    Generation gives the placeholder) are what the fakes of another process answer.
    """
    jev = await client.post("/v1/test/fakes/decisions.jev/script", json=JEV_SCRIPT)
    assert jev.status_code == 204, jev.text
    generation = await client.post("/v1/test/fakes/generation/script", json=GENERATION_SCRIPT)
    assert generation.status_code == 204, generation.text

    seen = await _run_child(db.app)

    label = seen["label"]
    assert label["type"] == "choice"
    assert label["choice"] == "hybrid"
    assert label["confidence"] == pytest.approx(0.93)
    assert label["probabilities"]["hybrid"] == pytest.approx(0.93)
    assert sum(label["probabilities"].values()) == pytest.approx(1.0)
    assert seen["latency_ms"] == 250
    assert seen["first_action"] == PLACEHOLDER


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_fake_script_route_exists_only_with_fakes(db: DbUrls, clock: FixedClock) -> None:
    """T-P0-04-18
    With real adapters the route is 404; with fakes a valid script is 204, an adapter with
    no scriptable fake is 404 and a script its fake cannot read is 422.
    """
    from tumnis.app import create_app  # noqa: PLC0415
    from tumnis.core import db as core_db  # noqa: PLC0415

    path = "/v1/test/fakes/decisions.jev/script"
    try:
        for adapters, expected in (("real", 404), ("fake", 204)):
            app = create_app(settings=settings_for(db, tumnis_adapters=adapters), clock=clock)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
                response = await client.post(path, json=JEV_SCRIPT)
                assert response.status_code == expected, (adapters, response.text)
                if adapters == "real":
                    continue
                unknown = await client.post("/v1/test/fakes/nothing.here/script", json={})
                assert unknown.status_code == 404, unknown.text
                for bad in (
                    {"question": "not_a_point", "answer": "hybrid"},
                    {**JEV_SCRIPT, "confidence": 1.5},
                    {**JEV_SCRIPT, "latency_ms": -1},
                    {**JEV_SCRIPT, "unexpected": True},
                ):
                    refused = await client.post(path, json=bad)
                    assert refused.status_code == 422, (bad, refused.text)
                missing = await client.post("/v1/test/fakes/generation/script", json={})
                assert missing.status_code == 422, missing.text
    finally:
        await core_db.dispose()


@pytest.mark.req("REL-7")
@pytest.mark.wp("P0-04")
async def test_a_later_script_replaces_the_earlier_and_reset_clears_them(
    client: httpx.AsyncClient,
) -> None:
    """T-P0-04-19
    A second script for the same adapter and key replaces the first; `POST /v1/test/reset`
    removes every script, so the fakes answer their defaults again.
    """
    from tumnis.core import fake_scripts  # noqa: PLC0415

    path = "/v1/test/fakes/decisions.jev/script"
    assert (await client.post(path, json=JEV_SCRIPT)).status_code == 204
    human = {**JEV_SCRIPT, "answer": "human"}
    assert (await client.post(path, json=human)).status_code == 204
    generation = await client.post("/v1/test/fakes/generation/script", json=GENERATION_SCRIPT)
    assert generation.status_code == 204, generation.text

    fake_scripts.enable()
    try:
        stored = await fake_scripts.lookup("decisions.jev", "quick_add_label")
        assert stored is not None
        assert stored["answer"] == "human"
        assert await fake_scripts.lookup("generation") is not None

        reset = await client.post("/v1/test/reset")
        assert reset.status_code == 204, reset.text

        assert await fake_scripts.lookup("decisions.jev", "quick_add_label") is None
        assert await fake_scripts.lookup("generation") is None
    finally:
        fake_scripts.disable()
