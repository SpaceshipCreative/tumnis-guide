"""Hostile packets (P2-11, SAF-6): every case of the hostile set, and every benign twin,
injected the way outside content reaches a task, renders only inside an untrusted block
of a tainted packet. This is the cheap per-PR half of the hostile suite: the packet side,
with no model (the skill side runs on the homelab runner)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

pytestmark = [pytest.mark.integration, pytest.mark.enable_socket]

BLOCK_RE = re.compile(
    r'<untrusted-data id="(u-[0-9a-f]{16})"[^<>]*>\n(.*?)\n</untrusted-data id="\1">', re.DOTALL
)
MIN_LINE = 12  # characters: a payload line this long is distinctive enough to search for


@pytest.mark.req("SAF-6")
@pytest.mark.wp("P2-11")
@pytest.mark.xfail(strict=True, reason="spec:P2-11")
async def test_every_case_renders_inside_one_block_and_taints(
    db: DbUrls, workspace: WorkspaceHandle, clock: FixedClock
) -> None:
    """T-P2-11-05
    Every hostile case and benign twin in backend/fixtures/hostile, injected through its
    `inject_as` path into a fresh task (a linked message or note, a document passage, a
    tainted task's title, a digest entry), gives a tainted task packet. Each injected text
    sits inside exactly one untrusted block of the prompt, whose unescaped body holds it,
    and no line of it appears anywhere in the prompt outside the blocks.
    """
    from tests._hostile import hostile_items, hostile_world  # noqa: PLC0415
    from tumnis.modules.agents.rules import unescape_untrusted  # noqa: PLC0415

    items = hostile_items()
    assert len(items) >= 32  # 16 or more cases, each with its twin
    async with hostile_world(db, workspace, clock) as world:
        for item in items:
            packet = await world.packet(item)
            assert packet.tainted, item.id
            prompt = packet.prompt_text
            bodies = [unescape_untrusted(found.group(2)) for found in BLOCK_RE.finditer(prompt)]
            outside = BLOCK_RE.sub("", prompt)
            for text in item.texts:
                holders = [body for body in bodies if text.strip() in body]
                assert len(holders) == 1, (item.id, len(holders))
                for line in text.splitlines():
                    if len(line.strip()) >= MIN_LINE:
                        assert line.strip() not in outside, (item.id, line)
