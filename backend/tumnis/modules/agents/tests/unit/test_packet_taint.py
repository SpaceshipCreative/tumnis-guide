"""A packet's `tainted` flag is computed from its blocks, never remembered (P2-02, SAF-1)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

NONCE = "u-00112233445566ff"
SOURCES = ("user", "task", "comment", "brief", "document", "email", "chat", "note", "agent")


def _inputs(
    *, task_tainted: bool, comments: list[bool], passages: list[bool], items: list[bool]
) -> dict[str, Any]:
    return {
        "task": {
            "id": "01950000-0000-7000-8000-00000000a001",
            "parent_id": None,
            "title": "Draft the Acme status note",
            "acceptance_criteria": "One page, plain words.",
            "label": "ai",
            "estimate_minutes": None,
            "status": "today",
            "tainted": task_tainted,
            "by_user": not task_tainted,
        },
        "comments": [
            {"author": "api_key:01950000-0000-7000-8000-00000000c001", "body": "c", "tainted": t}
            for t in comments
        ],
        "project": {
            "id": "01950000-0000-7000-8000-00000000b001",
            "name": "Acme site",
            "brief_md": "Keep the Acme site running.",
        },
        "passages": [
            {"document_id": "01950000-0000-7000-8000-00000000d001", "text": "p", "tainted": t}
            for t in passages
        ],
        "context_items": [
            {
                "id": f"01950000-0000-7000-8000-0000000e{n:04d}",
                "target_type": "message",
                "source": "email",
                "text": "m",
                "tainted": t,
            }
            for n, t in enumerate(items)
        ],
        "policy": {
            "gated": ["send_email"],
            "allowed": ["read"],
            "tool_allowlist": ["tumnis"],
            "time_cap_minutes": 60,
            "max_tasks_per_run": 20,
            "max_delegation_depth": 3,
            "tainted_run_all_gated": True,
        },
    }


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
@pytest.mark.xfail(strict=True, reason="spec:P2-02")
@settings(max_examples=300, deadline=None)
@given(
    blocks=st.lists(
        st.fixed_dictionaries(
            {
                "trust": st.sampled_from(["trusted", "untrusted"]),
                "tainted": st.booleans(),
                "source": st.sampled_from(SOURCES),
                "rendered": st.text(max_size=20),
            }
        ),
        max_size=12,
    ),
    task_tainted=st.booleans(),
    comments=st.lists(st.booleans(), max_size=4),
    passages=st.lists(st.booleans(), max_size=4),
    items=st.lists(st.booleans(), max_size=4),
)
def test_packet_tainted_is_or_of_blocks(
    blocks: list[dict[str, Any]],
    task_tainted: bool,
    comments: list[bool],
    passages: list[bool],
    items: list[bool],
) -> None:
    """T-P2-02-08
    Property: for random block lists, `packet_tainted == any(block.tainted)`; and a packet
    assembled from random inputs is tainted exactly when one of its blocks is.
    """
    from tumnis.modules.agents.packet_builder import (  # noqa: PLC0415
        PacketInputs,
        assemble,
        packet_blocks,
    )
    from tumnis.modules.agents.rules import Block, RunKind, packet_tainted  # noqa: PLC0415

    models = [Block.model_validate(b) for b in blocks]
    assert packet_tainted(models) == any(b.tainted for b in models)

    packet = assemble(
        PacketInputs.model_validate(
            _inputs(task_tainted=task_tainted, comments=comments, passages=passages, items=items)
        ),
        kind=RunKind.TASK,
        run_id="01950000-0000-7000-8000-00000000f001",
        profile_id="01950000-0000-7000-8000-00000000f002",
        nonce=NONCE,
        token=None,
    )
    every = packet_blocks(packet)
    assert packet.tainted == any(b.tainted for b in every)
    assert packet.tainted == (task_tainted or any(comments) or any(passages) or any(items))


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-02")
@pytest.mark.xfail(strict=True, reason="spec:P2-02")
def test_tainted_task_text_is_an_untrusted_block() -> None:
    """T-P2-02-09
    A tainted task's own title and criteria are rendered as an untrusted block (source
    `task`) in the body and in the prompt; the same task untainted and written by the
    user is a trusted block.
    """
    from tumnis.modules.agents.packet_builder import PacketInputs, assemble  # noqa: PLC0415
    from tumnis.modules.agents.rules import RunKind  # noqa: PLC0415

    def build(tainted: bool) -> Any:
        return assemble(
            PacketInputs.model_validate(
                _inputs(task_tainted=tainted, comments=[], passages=[], items=[])
            ),
            kind=RunKind.TASK,
            run_id="01950000-0000-7000-8000-00000000f001",
            profile_id="01950000-0000-7000-8000-00000000f002",
            nonce=NONCE,
            token=None,
        )

    tainted = build(True)
    text = tainted.body["task"]["text"]
    assert text["trust"] == "untrusted"
    assert text["tainted"] is True
    assert text["source"] == "task"
    assert text["rendered"].startswith(f'<untrusted-data id="{NONCE}" source="task"')
    block = re.search(
        rf'<untrusted-data id="{NONCE}" source="task"[^<>]*>\n([^<>]*)\n'
        rf'</untrusted-data id="{NONCE}">',
        tainted.prompt_text,
    )
    assert block is not None
    assert "Draft the Acme status note" in block.group(1)
    assert "One page, plain words." in block.group(1)
    assert tainted.tainted is True

    clean = build(False)
    assert clean.body["task"]["text"]["trust"] == "trusted"
    assert clean.body["task"]["text"]["tainted"] is False
    assert "Draft the Acme status note" in clean.body["task"]["text"]["rendered"]
    assert "<untrusted-data" not in clean.body["task"]["text"]["rendered"]
    assert clean.tainted is False
