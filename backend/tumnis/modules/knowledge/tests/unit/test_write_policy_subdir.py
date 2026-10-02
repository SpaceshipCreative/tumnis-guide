"""WritePolicy refuses a Tumnis subfolder that would not work as a path prefix (P3-14
review follow-up, FR-15.12): `may_write` checks `path.startswith(tumnis_subdir)`, so a
subfolder without its trailing '/' would let `TumnisX/a.md` through."""

import pytest

from tumnis.modules.knowledge.rules import WritePolicy, may_write


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
@pytest.mark.parametrize("subdir", ["", "/", "Tumnis", "/Tumnis/", "../Tumnis/", "a//b/", "./"])
def test_unsafe_tumnis_subdir_refused(subdir: str) -> None:
    with pytest.raises(ValueError, match="tumnis_subdir"):
        WritePolicy(mode="existing", tumnis_subdir=subdir)


@pytest.mark.req("FR-15.12")
@pytest.mark.wp("P3-14")
def test_nested_tumnis_subdir_accepted() -> None:
    policy = WritePolicy(mode="existing", tumnis_subdir="Work/Tumnis/")
    assert may_write(policy, "Work/Tumnis/a.md", None)
    assert not may_write(policy, "Work/TumnisX/a.md", None)
