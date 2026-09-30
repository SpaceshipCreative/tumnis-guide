"""`tumnis decisions eval` (P3-08, Quality: decision quality): the offline evaluation
reproduces the calibration page's numbers from a stored set, and keeps each run in
`decision_evals` with the set's hash and the model version."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from pathlib import Path

    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncSession
    from typer.testing import Result

    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]


def _key(evaluation: dict[str, Any]) -> tuple[str, str, str]:
    return (evaluation["decision_point"], evaluation["provider"], evaluation["model_version"])


def _page_evaluations(page: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted((e for p in page["points"] for e in p["evaluations"]), key=_key)


async def _eval(
    args: list[str], db: DbUrls, clock: FixedClock, monkeypatch: pytest.MonkeyPatch
) -> Result:
    """Run `tumnis decisions eval ...` on the test database, on the test's clock, in a
    thread (the command runs its own event loop). The CLI is imported by name: it wires
    every module, and decisions' tests reach other modules only through their api."""
    from typer.testing import CliRunner  # noqa: PLC0415

    cli = importlib.import_module("tumnis.cli")
    monkeypatch.setattr(cli, "make_clock", lambda: clock)
    env = {"DATABASE_URL": db.app, "DATABASE_DIRECT_URL": db.app, "DEPLOYMENT_ENV": "dev"}

    def invoke() -> Result:
        return CliRunner().invoke(cli.app, ["decisions", "eval", *args], env=env)

    return await asyncio.to_thread(invoke)


async def _evaluations_from_cli(
    tmp_path: Path,
    workspace: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
    """Export the log to a set with `--from-log --export`, then evaluate the set: the
    exported file and both runs' evaluations, sorted."""
    out = tmp_path / "out.jsonl"
    ws = ["--workspace", str(workspace.id)]
    exported = await _eval(["--from-log", "--export", str(out), *ws], db, clock, monkeypatch)
    assert exported.exit_code == 0, exported.output
    rerun = await _eval(["--set", str(out), *ws], db, clock, monkeypatch)
    assert rerun.exit_code == 0, rerun.output
    return (
        out,
        sorted(json.loads(exported.stdout)["evaluations"], key=_key),
        sorted(json.loads(rerun.stdout)["evaluations"], key=_key),
    )


@pytest.mark.req("Quality: decision quality")
@pytest.mark.wp("P3-08")
async def test_offline_eval_reproduces_page_numbers(  # noqa: PLR0917
    app: FastAPI,
    session_client: SessionClient,
    workspace: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    make_decision_log: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """T-P3-08-07
    Given 150 logged decisions with the human's decisions on them, the calibration page's
    evaluations (one per point, provider and model, with at least one at 100 labeled or
    more), `tumnis decisions eval --from-log --export out.jsonl` and `tumnis decisions eval
    --set out.jsonl` give the same evaluations, field for field. The exported set holds
    the input hash, never the input.
    """
    await make_decision_log(150, 0.8)
    page = await session_client.get("/v1/decisions/calibration")
    assert page.status_code == 200, page.text
    expected = _page_evaluations(page.json())
    assert any(e["metrics"] is not None for e in expected), expected
    assert any(e["provider"] == "vllm" for e in expected), expected

    out, from_log, from_set = await _evaluations_from_cli(
        tmp_path, workspace, db, clock, monkeypatch
    )

    assert from_log == expected
    assert from_set == expected
    first = json.loads(out.read_text().splitlines()[0])
    assert set(first) >= {
        "decision_point",
        "model_version",
        "provider",
        "input_hash",
        "answer",
        "confidence",
        "truth",
    }
    assert len(first["input_hash"]) == 64


@pytest.mark.req("Quality: decision quality")
@pytest.mark.wp("P3-08")
async def test_eval_result_stored_with_model_version(  # noqa: PLR0917
    workspace: WorkspaceHandle,
    db: DbUrls,
    clock: FixedClock,
    core_db: None,
    make_decision_log: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    owner_session: AsyncSession,
) -> None:
    """T-P3-08-08
    Each run keeps one `decision_evals` row per evaluated point, provider and model: the
    model version, the sha256 of the evaluated set's bytes (the same for the export and
    the rerun on it), the metrics as shown, and the run time from the clock.
    """
    await make_decision_log(150, 0.8)
    out, _, from_set = await _evaluations_from_cli(tmp_path, workspace, db, clock, monkeypatch)

    set_sha256 = hashlib.sha256(out.read_bytes()).hexdigest()
    rows = (
        await owner_session.execute(
            text(
                "SELECT workspace_id, decision_point, provider, model_version, set_sha256, "
                "metrics, run_at FROM decision_evals ORDER BY created_at, id"
            )
        )
    ).all()
    assert len(rows) == 2 * len(from_set), rows
    assert {r.set_sha256 for r in rows} == {set_sha256}
    assert {r.workspace_id for r in rows} == {workspace.id}
    assert {r.run_at for r in rows} == {clock.now()}
    stored = {
        (r.decision_point, r.provider, r.model_version): r.metrics for r in rows[len(from_set) :]
    }
    assert stored == {_key(e): e["metrics"] for e in from_set}
    assert ("project_match", "jev", "jev-1.13.0") in stored
