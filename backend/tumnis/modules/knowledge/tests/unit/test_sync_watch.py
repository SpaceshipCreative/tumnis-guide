"""Which location a watched change belongs to (P1-15, `local_watch`): a change inside a
project folder queues its location's sync; Tumnis's own files and OS droppings do not."""

import pytest

from tumnis.modules.knowledge.sync import ignored, watched_location

ROOTS = [
    ("ws-1", "loc-a", "/data/projects"),
    ("ws-2", "loc-b", "/data/other/"),
]


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/data/projects/Acme/notes/Plan.md", ("ws-1", "loc-a")),
        ("/data/projects/Acme/uploads/terms.txt", ("ws-1", "loc-a")),
        ("/data/other/Site/a.pdf", ("ws-2", "loc-b")),
        ("/data/projects/Acme", None),  # the folder itself
        ("/data/projects/Acme/.tumnis/trash/x.md", None),
        ("/data/projects/Acme/notes/.tumnis-tmp-123", None),
        ("/data/projects/Acme/.DS_Store", None),
        ("/data/projects/Acme/~$report.docx", None),
        ("/data/projectsX/Acme/a.md", None),  # a sibling of the root, not inside it
        ("/elsewhere/a.md", None),
    ],
)
def test_watched_location(path: str, expected: tuple[str, str] | None) -> None:
    assert watched_location(path, ROOTS) == expected


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P1-15")
def test_ignored_names() -> None:
    assert ignored(".tumnis/state.json")
    assert ignored("uploads/Thumbs.db")
    assert not ignored("notes/Plan.md")
    assert not ignored("notes/.tumnis/x.md")  # only the folder's own .tumnis/ is Tumnis's
