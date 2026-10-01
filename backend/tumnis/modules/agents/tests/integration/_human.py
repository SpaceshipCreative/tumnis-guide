"""Helpers for the P2-05 tests (questions and approvals). No assertions live here:
spec-guard locks the test bodies, and these helpers adapt to the agents api and routes.

- `human_waits(poll_seconds=..., slice_seconds=...)`: the long poll of `ask_human` and
  `request_approval`, and the re-arm interval of the human waits (R-30), for the test;
  put back to the defaults afterwards.
- `started(world, db, task_id)`: a run of the task, dispatched and running.
- `ask(app, token, run_id, prompt, ...)`, `approval(app, token, run_id, action, ...)`: the
  agent's calls through the REST twins (`POST /v1/runs/{run_id}/questions` and
  `/approvals`) with its task token and a fresh Idempotency-Key.
- `open_items(db, kind)`: the open review items of a kind, as the owner reads them.
- `decide(session_client, item, action, payload)`: R-04's decide at the item's version.
- `count(db, sql, params)`, `status_of(db, table, id)`: owner reads.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from tests._mcp import http_for
from tumnis.modules.agents.tests.integration._runs import owner_rows, wait_until

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tumnis.modules.agents.tests.integration._runs import RunWorld

Json = dict[str, Any]


@contextlib.contextmanager
def human_waits(
    *, poll_seconds: float | None = None, slice_seconds: float | None = None
) -> Iterator[None]:
    from tumnis.modules.agents import api  # noqa: PLC0415

    api.configure_human_waits(poll_seconds=poll_seconds, wait_slice_seconds=slice_seconds)
    try:
        yield
    finally:
        api.configure_human_waits()


async def started(world: RunWorld, db: DbUrls, task_id: uuid.UUID) -> uuid.UUID:
    """Requests a run of the task and waits until the runner has it and it runs."""
    run_id = await world.request(task_id)
    world.runner.wait_for(lambda r: any(m.run_id == run_id for m in r.runs()))
    await wait_until(
        lambda: owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,)) == [("running",)]
    )
    return run_id


async def _post(app: FastAPI, token: str, path: str, body: Json) -> httpx.Response:
    async with http_for(app, token) as http:
        return await http.post(
            path, json=body, headers={"Idempotency-Key": f"agent-{uuid.uuid4()}"}, timeout=120
        )


async def ask(
    app: FastAPI,
    token: str,
    run_id: uuid.UUID,
    prompt: str,
    *,
    question_id: str | None = None,
    choices: list[str] | None = None,
) -> httpx.Response:
    body: Json = {"prompt": prompt}
    if question_id is not None:
        body["question_id"] = question_id
    if choices is not None:
        body["choices"] = choices
    return await _post(app, token, f"/v1/runs/{run_id}/questions", body)


async def approval(  # the request's fields, spelled out
    app: FastAPI,
    token: str,
    run_id: uuid.UUID,
    action_class: str,
    description: str = "Do the thing",
    *,
    approval_id: str | None = None,
    target: str | None = None,
) -> httpx.Response:
    body: Json = {"action_class": action_class, "description": description}
    if approval_id is not None:
        body["approval_id"] = approval_id
    if target is not None:
        body["target"] = target
    return await _post(app, token, f"/v1/runs/{run_id}/approvals", body)


def open_items(db: DbUrls, kind: str) -> list[Json]:
    """The open review items of `kind` (id, version, payload, target), oldest first."""
    found = owner_rows(
        db,
        "SELECT id, version, payload, target_type, target_id FROM review_items"
        " WHERE kind = %s AND decided_at IS NULL AND deleted_at IS NULL ORDER BY created_at",
        (kind,),
    )
    return [
        {"id": r[0], "version": r[1], "payload": r[2], "target_type": r[3], "target_id": r[4]}
        for r in found
    ]


async def decide(
    http: SessionClient,
    item: Json,
    action: str,
    payload: Json | None = None,
    *,
    request_id: str | None = None,
) -> httpx.Response:
    body: Json = {"action": action, "version": item["version"]}
    if payload is not None:
        body["payload"] = payload
    headers = {"X-Request-ID": request_id} if request_id else None
    return await http.post(f"/v1/review/{item['id']}/decide", json=body, headers=headers)


def count(db: DbUrls, query: str, params: tuple[Any, ...] = ()) -> int:
    [(n,)] = owner_rows(db, query, params)
    return int(n)


def task_status(db: DbUrls, task_id: uuid.UUID) -> str:
    [(status,)] = owner_rows(db, "SELECT status::text FROM tasks WHERE id = %s", (task_id,))
    return str(status)


def run_status(db: DbUrls, run_id: uuid.UUID) -> str:
    [(status,)] = owner_rows(db, "SELECT status FROM runs WHERE id = %s", (run_id,))
    return str(status)


def outbox_count(db: DbUrls, name: str, **match: str) -> int:
    """How many outbox rows are named `name` (with payload fields equal to `match`)."""
    query = "SELECT count(*) FROM outbox WHERE name = %s"
    params: list[Any] = [name]
    for key, value in match.items():
        query += f" AND payload->>'{key}' = %s"
        params.append(value)
    return count(db, query, tuple(params))


async def taint(world: RunWorld, task_id: uuid.UUID) -> None:
    """Links an outside URL (a tainted context item) to the task, so its runs are tainted
    (P2-08, SAF-1); an invented address."""
    from tumnis.modules.integrations import api as integrations  # noqa: PLC0415

    await integrations.link_context(
        world.workspace.ctx,
        owner_type="task",
        owner_id=task_id,
        target_type="url",
        target_url=f"https://mail.example.com/threads/{uuid.uuid4().hex[:8]}",
        added_by=world.workspace.ctx.actor,
    )


def audit_rows(db: DbUrls, action: str) -> list[Json]:
    """The audit rows of `action`, oldest first."""
    found = owner_rows(
        db,
        "SELECT actor_type, actor_id, target_type, target_id, reason, correlation_id, details"
        " FROM audit_log WHERE action = %s ORDER BY seq",
        (action,),
    )
    keys = ("actor_type", "actor_id", "target_type", "target_id", "reason", "correlation_id")
    return [{**dict(zip(keys, r[:6], strict=True)), "details": r[6]} for r in found]


def use_decisions(jev: Any) -> None:
    """The decisions provider every `decide` in this process asks (the evaluation runs on
    the `dbos` fixture's threads); `use_decisions(None)` puts the real chain back."""
    from tumnis.modules.decisions import api as decisions  # noqa: PLC0415

    decisions.use_providers(None if jev is None else decisions.Providers(jev=jev, vllm=None))


def _fake_decisions() -> Any:
    """A fresh decisions fake. Loaded by name: agents may import only `decisions.api`
    (import-linter), and the fake is test scaffolding, never imported by the module."""
    import importlib  # noqa: PLC0415

    return importlib.import_module("tumnis.modules.decisions.adapters.fake").FakeDecisions()


def gated_noul(p: float) -> Any:
    """A fake Decisions provider whose `approval_need` answers "gated" with probability
    `p`; `p=None` makes it fail as unavailable (Decisions down)."""
    fake = _fake_decisions()
    fake.script("approval_need", {"gated": {"type": "noul", "noul": p}})
    return fake


def decisions_down() -> Any:
    from tumnis.core.adapters.errors import AdapterUnavailable  # noqa: PLC0415

    fake = _fake_decisions()
    fake.script("approval_need", fail=AdapterUnavailable("decisions.fake", "ask", "down"))
    return fake
