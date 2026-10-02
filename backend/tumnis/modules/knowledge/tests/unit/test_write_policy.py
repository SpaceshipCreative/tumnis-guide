"""The existing-folder write rules (P3-14, FR-15.12): in a folder the user already keeps,
Tumnis writes only inside `Tumnis/`, never renames, moves or overwrites a file it did not
create, and deletes one only when the user confirms it (never at an agent's request)."""

from __future__ import annotations

import itertools

import pytest
from hypothesis import given
from hypothesis import strategies as st

MODES = ("tumnis_made", "existing")
ORIGINS = ("tumnis", "external", None)
PATHS = (
    "Tumnis/notes/Plan.md",
    "Tumnis/uploads/a.pdf",
    "Tumnis/.trash/old.md",
    "notes/Plan.md",
    "Contracts/SOW.pdf",
    "notes.md",
    "tumnis/notes/Plan.md",  # not Tumnis/: the name is compared exactly
    "Tumnis",  # the folder itself, not a file inside it
    "TumnisX/a.md",
    "../Tumnis/a.md",
    "Tumnis/../a.md",
    "/Tumnis/a.md",
    "",
)


def _write_expected(mode: str, path: str, origin: str | None) -> bool:
    if origin == "external":
        return False
    safe = path in {
        "Tumnis/notes/Plan.md",
        "Tumnis/uploads/a.pdf",
        "Tumnis/.trash/old.md",
        "notes/Plan.md",
        "Contracts/SOW.pdf",
        "notes.md",
        "tumnis/notes/Plan.md",
        "Tumnis",
        "TumnisX/a.md",
    }
    if mode == "tumnis_made":
        return safe
    return path in {"Tumnis/notes/Plan.md", "Tumnis/uploads/a.pdf", "Tumnis/.trash/old.md"}


DELETES = {
    # (origin, actor, confirmed_by_user) -> outcome, in either mode
    ("tumnis", "user", False): "trash",
    ("tumnis", "user", True): "trash",
    ("tumnis", "agent", False): "trash",
    ("tumnis", "agent", True): "trash",
    ("tumnis", "system", False): "trash",
    ("tumnis", "system", True): "trash",
    ("external", "user", False): "index_only",
    ("external", "user", True): "delete_at_source",
    ("external", "agent", False): "refuse",
    ("external", "agent", True): "refuse",  # an agent can never delete an outside file
    ("external", "system", False): "index_only",
    ("external", "system", True): "delete_at_source",  # the sync carrying out the user's
}


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
def test_write_rename_delete_tables() -> None:
    """T-P3-14-04
    Every (mode, path, origin) for `may_write`, every (mode, origin) for `may_rename` and
    every (mode, origin, actor, confirmed) for `may_delete` follows the tables; the trash
    of a Tumnis-made folder is `.tumnis/trash/`, an existing folder's `Tumnis/.trash/`.
    """
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        ActorKind,
        WritePolicy,
        may_delete,
        may_rename,
        may_write,
        trash_dir,
    )

    for mode, path, origin in itertools.product(MODES, PATHS, ORIGINS):
        policy = WritePolicy(mode=mode)  # type: ignore[arg-type]
        assert policy.tumnis_subdir == "Tumnis/"
        assert may_write(policy, path, origin) is _write_expected(mode, path, origin), (  # type: ignore[arg-type]
            mode,
            path,
            origin,
        )
    for mode in MODES:
        policy = WritePolicy(mode=mode)  # type: ignore[arg-type]
        assert may_rename(policy, "tumnis") is True
        assert may_rename(policy, "external") is False
        for (origin, actor, confirmed), outcome in DELETES.items():
            got = may_delete(policy, origin, ActorKind(actor), confirmed)
            assert got == outcome, (mode, origin, actor, confirmed)
    assert {a.value for a in ActorKind} == {"user", "agent", "system"}
    assert trash_dir(WritePolicy(mode="tumnis_made")) == ".tumnis/trash/"
    assert trash_dir(WritePolicy(mode="existing")) == "Tumnis/.trash/"


_PIECES = st.sampled_from(
    ["Tumnis", "tumnis", "TUMNIS", "Tumnis ", " Tumnis", "Tumnis\u200b", "Tumn\u0131s", "a", "..",
     ".", "", "~", "notes", "\uff0e\uff0e", "Tumnis\u2215x", "C:", "x.md", "\u202e", "Tumnis\\x"]
)  # fmt: skip


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@given(
    parts=st.lists(st.one_of(_PIECES, st.text(max_size=8)), min_size=0, max_size=5),
    lead=st.sampled_from(["", "/", "./", "../"]),
    origin=st.sampled_from(ORIGINS),
)
def test_existing_mode_never_writes_outside_tumnis(
    parts: list[str], lead: str, origin: str | None
) -> None:
    """T-P3-14-05
    Any path (look-alike names, dots, separators, case changes): when `may_write` allows it
    in an existing folder, the path is safe and lies under `Tumnis/` exactly, with a file
    name after it; an outside file is never written.
    """
    from tumnis.modules.knowledge.rules import (  # noqa: PLC0415
        WritePolicy,
        may_write,
        safe_rel_path,
    )

    path = lead + "/".join(parts)
    if may_write(WritePolicy(mode="existing"), path, origin):  # type: ignore[arg-type]
        assert origin != "external"
        assert safe_rel_path(path) == path
        assert path.startswith("Tumnis/")
        assert len(path) > len("Tumnis/")
