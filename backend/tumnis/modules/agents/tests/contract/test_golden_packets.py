"""Golden task packets (R-24). P2-07 adds the code location; P2-02 adds the golden packets
proper (T-P2-02-01 to 03) to this file.

The code location sits where P2-02's schema puts it, `body.project.code_location`: a `path`
on the agent server, or a `repo` clone URL with its default branch, never both. The
daemon reads it there to prepare the run's worktree (`workdir_policy: worktree`).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

pytestmark = [pytest.mark.contract]

# case -> (code_path, repo_url, default_branch, expected code_location)
CASES: dict[str, tuple[str | None, str | None, str | None, dict[str, Any] | None]] = {
    "path": (
        "/home/tumnis-agent/code/acme-site",
        None,
        None,
        {"kind": "path", "path": "/home/tumnis-agent/code/acme-site"},
    ),
    "repo": (
        None,
        "https://code.example.org/acme/site.git",
        "main",
        {
            "kind": "repo",
            "clone_url": "https://code.example.org/acme/site.git",
            "default_branch": "main",
        },
    ),
    "none": (None, None, None, None),
}


@pytest.mark.req("FR-2.1")
@pytest.mark.wp("P2-07")
@pytest.mark.xfail(strict=True, reason="spec:P2-07")
@pytest.mark.parametrize("case", list(CASES))
def test_packet_carries_code_location(case: str) -> None:
    """T-P2-07-13
    A packet for a project with a path carries the path; with a repository, the clone URL
    and the default branch; with neither, a null location. Never both: a project naming
    both is refused before any packet is built. A packet with a location asks the daemon
    for a worktree; one without runs with `workdir_policy: none`.
    """
    from tumnis.modules.agents.api import RunKind, SchemaRef, TaskPacket  # noqa: PLC0415
    from tumnis.modules.agents.packet_builder import (  # type: ignore[attr-defined]  # noqa: PLC0415
        code_location,
        code_location_of,
        workdir_policy,
    )

    code_path, repo_url, branch, expected = CASES[case]
    location = code_location(code_path, repo_url, default_branch=branch)
    run_id = uuid.uuid4()
    packet = TaskPacket(
        kind=RunKind.TASK,
        run_id=run_id,
        profile_id=uuid.uuid4(),
        skill="work",
        output_schema=SchemaRef(family="result", name="task_result", version=1),
        correlation_id=f"run:{run_id}",
        timeout_s=3600,
        prompt_text="Use the skill work.\n<packet>\n{}\n</packet>\n",
        body={
            "project": {
                "code_location": None if location is None else location.model_dump(mode="json")
            }
        },
    )
    data = json.loads(packet.model_dump_json())
    carried = data["body"]["project"]["code_location"]
    assert carried == expected
    if carried is not None:
        assert ("path" in carried) != ("clone_url" in carried)
    assert code_location_of(packet) == location
    assert workdir_policy(packet) == ("none" if expected is None else "worktree")

    with pytest.raises(ValueError, match="not both") as refused:
        code_location("/home/tumnis-agent/code/acme-site", "https://code.example.org/a.git")
    assert getattr(refused.value, "code", None) == "code_location_conflict"
