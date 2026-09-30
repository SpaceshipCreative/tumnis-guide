"""Taint rules (P2-08, SAF-1, FR-4.5, design decision 14): whatever is made from tainted
input is tainted (`derive_taint` is the OR of its sources), linking only raises taint
(`raise_only`), and no tainted task may run unattended (`may_run_unattended`, which P4-04's
scheduler calls and only ever adds checks to)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

KINDS = ("context_item", "parent_task", "run", "document", "proposal", "user")


@pytest.mark.req("SAF-1")
@pytest.mark.wp("P2-08")
@given(
    picks=st.lists(
        st.tuples(st.sampled_from(KINDS), st.booleans(), st.uuids() | st.none()), max_size=20
    )
)
def test_derive_taint_is_or(picks: list[tuple[str, bool, object]]) -> None:
    """T-P2-08-02
    For any list of sources (any kinds, ids and taints, including none at all), the
    derived taint is true exactly when one source is tainted; the order does not matter,
    and `raise_only` never lowers the current taint.
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        TaintSource,
        derive_taint,
        raise_only,
    )

    sources = [TaintSource(kind=kind, id=ident, tainted=tainted) for kind, tainted, ident in picks]  # type: ignore[arg-type]
    expected = any(tainted for _kind, tainted, _ident in picks)
    assert derive_taint(sources) is expected
    assert derive_taint(reversed(sources)) is expected
    assert derive_taint(iter(sources)) is expected
    for current in (False, True):
        for incoming in (False, True):
            assert raise_only(current, incoming) is (current or incoming)
    assert raise_only(True, False) is True


@pytest.mark.req("FR-4.5")
@pytest.mark.wp("P2-08")
@given(data=st.data())
def test_may_run_unattended_false_for_every_tainted_task(data: st.DataObject) -> None:
    """T-P2-08-03
    Whatever else a tainted task's view says (any label, including none, and any status),
    `may_run_unattended` is false. (P4-04 adds its own checks on top, so nothing here says
    when an untainted task may run.)
    """
    from tumnis.modules.tasks.rules import (  # noqa: PLC0415
        Label,
        Status,
        TaskView,
        may_run_unattended,
    )

    view = data.draw(
        st.builds(
            TaskView,
            tainted=st.just(True),
            label=st.sampled_from([None, *Label]),
            status=st.sampled_from(list(Status)),
        )
    )
    assert may_run_unattended(view) is False
