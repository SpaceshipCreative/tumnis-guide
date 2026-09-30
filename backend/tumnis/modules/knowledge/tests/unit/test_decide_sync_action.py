"""`decide_sync_action` decides every case of the architecture's sync table (P1-15,
FR-15.12): one row per case, and two invariants over any inputs. Tumnis never overwrites a
file it did not create, and every write carries a precondition."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given
from hypothesis import strategies as st

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 3, 9, 13, 0, tzinfo=UTC)
DAY = date(2026, 3, 9)
DOC = UUID("0190a7a0-0000-7000-8000-000000000001")
PATH = "notes/Plan.md"
CONFLICT = "notes/Plan (conflict 2026-03-09).md"
SIBLINGS = frozenset({PATH})


def _prev(**kw: Any) -> dict[str, Any]:
    base = {
        "path": PATH,
        "size": 10,
        "mtime": T0,
        "etag": "e1",
        "content_hash": "h1",
        "origin": "tumnis",
        "document_id": DOC,
        "synced_version": 3,
    }
    return base | kw


def _remote(**kw: Any) -> dict[str, Any]:
    base = {"size": 10, "mtime": T0, "etag": "e1", "content_hash": None}
    return base | kw


def _local(**kw: Any) -> dict[str, Any]:
    base = {
        "document_id": DOC,
        "version": 3,
        "content_hash": "h1",
        "origin": "tumnis",
        "trashed": False,
        "delete_confirmed": False,
    }
    return base | kw


CHANGED = {"size": 12, "mtime": T1, "etag": "e2", "content_hash": "h2"}  # edited outside
TOUCHED = {"mtime": T1, "content_hash": "h1"}  # same bytes, new mtime
EDITED = {"version": 4, "content_hash": "h3"}  # edited in Tumnis since the last sync
EXTERNAL = {"origin": "external"}
TRASHED = {"trashed": True}

# (row, prev, remote, local, expected SyncDecision fields beyond the defaults)
ROWS: list[tuple[str, Any, Any, Any, dict[str, Any]]] = [
    ("01-new-outside-file", None, _remote(content_hash="h1"), None,
     {"action": "create_document", "taint_new_document": True}),
    ("02-new-in-tumnis", None, None, _local(),
     {"action": "write_new"}),
    ("03-crash-between-write-and-record", None, _remote(content_hash="h1"), _local(),
     {"action": "adopt"}),
    ("04-both-new-other-hash", None, _remote(content_hash="h2"), _local(),
     {"action": "conflict_keep_both", "conflict_path": CONFLICT, "review_kind": "sync_conflict"}),
    ("05-unchanged", _prev(), _remote(), _local(),
     {"action": "noop"}),
    ("06-metadata-only", _prev(), _remote(**TOUCHED), _local(),
     {"action": "update_stat"}),
    ("07-changed-outside", _prev(), _remote(**CHANGED), _local(),
     {"action": "new_version_from_folder"}),
    ("08-changed-in-tumnis", _prev(), _remote(), _local(**EDITED),
     {"action": "write_through", "if_match": "e1"}),
    ("09-changed-in-tumnis-external", _prev(**EXTERNAL), _remote(), _local(**EDITED, **EXTERNAL),
     {"action": "conflict_keep_both", "conflict_path": CONFLICT, "review_kind": "sync_conflict"}),
    ("10-changed-both-tumnis", _prev(), _remote(**CHANGED), _local(**EDITED),
     {"action": "conflict_keep_both", "if_match": "e2", "conflict_path": CONFLICT,
      "review_kind": "sync_conflict"}),
    ("11-changed-both-external", _prev(**EXTERNAL), _remote(**CHANGED),
     _local(**EDITED, **EXTERNAL),
     {"action": "conflict_keep_both", "conflict_path": CONFLICT, "review_kind": "sync_conflict"}),
    ("12-deleted-outside", _prev(), None, _local(),
     {"action": "trash_document"}),
    ("13-deleted-outside-edited-inside", _prev(), None, _local(**EDITED),
     {"action": "rewrite_from_tumnis", "review_kind": "deleted_outside_edited_inside"}),
    ("14-trashed-tumnis-made", _prev(), _remote(), _local(**TRASHED),
     {"action": "move_to_tumnis_trash", "if_match": "e1"}),
    ("15-trashed-external", _prev(**EXTERNAL), _remote(), _local(**TRASHED, **EXTERNAL),
     {"action": "unindex_only"}),
    ("16-trashed-external-confirmed", _prev(**EXTERNAL), _remote(),
     _local(**TRASHED, **EXTERNAL, delete_confirmed=True),
     {"action": "delete_at_source", "if_match": "e1"}),
    ("17-edited-outside-trashed-inside", _prev(), _remote(**CHANGED), _local(**TRASHED),
     {"action": "restore_document", "review_kind": "edited_outside_deleted_inside"}),
    ("18-deleted-both", _prev(), None, _local(**TRASHED),
     {"action": "forget"}),
    ("19-orphaned-record", _prev(), None, None,
     {"action": "forget"}),
    ("20-nothing", None, None, None,
     {"action": "noop"}),
    ("20-nothing-but-trash", None, None, _local(**TRASHED),
     {"action": "noop"}),
]  # fmt: skip

DEFAULTS = {
    "if_match": None,
    "conflict_path": None,
    "review_kind": None,
    "taint_new_document": False,
}


def _decide(
    prev: dict[str, Any] | None,
    local: dict[str, Any] | None,
    remote: dict[str, Any] | None,
    *,
    path: str = PATH,
    siblings: frozenset[str] = SIBLINGS,
) -> Any:
    from tumnis.modules.knowledge.sync_rules import (  # type: ignore[import-untyped]  # red until P1-15 lands  # noqa: PLC0415
        Local,
        Prev,
        Remote,
        decide_sync_action,
    )

    return decide_sync_action(
        Prev(**prev) if prev is not None else None,
        Local(**local) if local is not None else None,
        Remote(**remote) if remote is not None else None,
        path=path,
        today_local=DAY,
        siblings=siblings,
    )


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@pytest.mark.parametrize(
    ("prev", "remote", "local", "expected"),
    [pytest.param(p, r, lo, e, id=row) for row, p, r, lo, e in ROWS],
)
def test_decision_table(
    prev: dict[str, Any] | None,
    remote: dict[str, Any] | None,
    local: dict[str, Any] | None,
    expected: dict[str, Any],
) -> None:
    """T-P1-15-01
    Rows 1 to 20 of the plan's decision table: the action and every field of the decision
    (precondition, conflict path, review kind, taint) for each combination of the last
    synced state, the folder now and the linked Document now.
    """
    decision = _decide(prev, local, remote)
    assert decision.model_dump(mode="json") == DEFAULTS | expected


# --- Invariants -------------------------------------------------------------------------

_etag = st.sampled_from(["e1", "e2"])
_hash = st.sampled_from(["h1", "h2", "h3"])
_mtime = st.sampled_from([T0, T1])
_siblings = st.sets(
    st.sampled_from(
        [
            PATH,
            CONFLICT,
            "notes/Plan (conflict 2026-03-09) 2.md",
            "notes/plan (CONFLICT 2026-03-09).md",
        ]
    )
).map(lambda found: frozenset(found | {PATH}))


@st.composite
def _inputs(
    draw: st.DrawFn, origin: st.SearchStrategy[str]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None, frozenset[str]]:
    shared_origin = draw(origin)
    prev = draw(
        st.none()
        | st.fixed_dictionaries(
            {
                "size": st.integers(1, 3),
                "mtime": _mtime,
                "etag": _etag,
                "content_hash": _hash,
                "synced_version": st.none() | st.integers(0, 5),
            }
        ).map(lambda d: _prev(**d, origin=shared_origin))
    )
    remote = draw(
        st.none()
        | st.fixed_dictionaries(
            {
                "size": st.integers(1, 3),
                "mtime": _mtime,
                "etag": _etag,
                "content_hash": st.none() | _hash,
            }
        ).map(lambda d: _remote(**d))
    )
    local = draw(
        st.none()
        | st.fixed_dictionaries(
            {
                "version": st.integers(0, 5),
                "content_hash": _hash,
                "trashed": st.booleans(),
                "delete_confirmed": st.booleans(),
            }
        ).map(lambda d: _local(**d, origin=shared_origin))
    )
    return prev, remote, local, draw(_siblings)


NEVER_FOR_EXTERNAL = {"write_through", "rewrite_from_tumnis", "move_to_tumnis_trash"}


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@given(inputs=_inputs(st.just("external")))
def test_never_overwrites_external_file(inputs: Any) -> None:
    """T-P1-15-02
    For any inputs whose file came from outside (`origin = "external"`), the action is never
    WRITE_THROUGH, REWRITE_FROM_TUMNIS or MOVE_TO_TUMNIS_TRASH, and DELETE_AT_SOURCE comes
    only with the user's in-app confirmation (`delete_confirmed`).
    """
    prev, remote, local, siblings = inputs
    decision = _decide(prev, local, remote, siblings=siblings)
    assert decision.action not in NEVER_FOR_EXTERNAL
    if decision.action == "delete_at_source":
        assert local is not None
        assert local["delete_confirmed"]


REPLACES = {"write_through", "move_to_tumnis_trash", "delete_at_source"}  # touch an existing file
CREATES = {"write_new", "rewrite_from_tumnis"}  # write where nothing is


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.xfail(strict=True, reason="spec:P1-15")
@given(inputs=_inputs(st.sampled_from(["tumnis", "external"])))
def test_every_write_has_a_precondition(inputs: Any) -> None:
    """T-P1-15-03
    Every decision that writes carries a precondition: a change to an existing file names
    the etag the folder shows now (`if_match`); a create is create-only (`if_match=None`,
    and only where the folder shows nothing); a conflict copy goes to a free name, never
    the path itself; and a decision that writes nothing carries no precondition.
    """
    prev, remote, local, siblings = inputs
    decision = _decide(prev, local, remote, siblings=siblings)
    if decision.action in REPLACES:
        assert remote is not None
        assert decision.if_match == remote["etag"]
    elif decision.action in CREATES:
        assert remote is None
        assert decision.if_match is None
    elif decision.action == "conflict_keep_both":
        assert decision.conflict_path is not None
        assert decision.conflict_path.casefold() not in {s.casefold() for s in siblings}
        if decision.if_match is not None:
            assert remote is not None
            assert decision.if_match == remote["etag"]
    else:
        assert decision.if_match is None
        assert decision.conflict_path is None
