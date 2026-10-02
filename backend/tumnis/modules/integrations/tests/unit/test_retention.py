"""The retention rules (P3-09, SAAS-2): `purge_cutoff` and `purge_candidates`.

SAAS-2's default keeps ingested content until its project is purged (no cutoff); the
opt-in `days` mode cuts at `now - days` (7 at least). Past the cutoff an item is still kept
while its project is archived (archive compresses, it never purges) or while it is linked
to an open task (plan default: a task never loses its context mid-work).
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

NOW = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


@pytest.mark.req("SAAS-2")
@pytest.mark.wp("P3-09")
def test_cutoff_and_candidates_table() -> None:
    """T-P3-09-01
    Default keeps everything; days mode cuts; archived and open-task-linked items kept.
    """
    from tumnis.modules.integrations.rules import (  # noqa: PLC0415
        IngestedLite,
        RetentionSetting,
        purge_candidates,
        purge_cutoff,
    )

    default = RetentionSetting()
    assert default.mode == "keep_until_project_purged"
    assert default.days is None
    assert purge_cutoff(NOW, default) is None

    thirty = RetentionSetting(mode="days", days=30)
    assert purge_cutoff(NOW, thirty) == NOW - timedelta(days=30)
    assert purge_cutoff(NOW, RetentionSetting(mode="days", days=7)) == NOW - timedelta(days=7)

    for bad in ({"mode": "days", "days": 6}, {"mode": "days"}, {"mode": "days", "days": 0}):
        with pytest.raises(ValidationError):
            RetentionSetting.model_validate(bad)

    cutoff = NOW - timedelta(days=30)
    old = NOW - timedelta(days=31)
    recent = NOW - timedelta(days=29)
    # (item, cutoff, project_archived) -> purged?
    table = [
        (IngestedLite(at=old), None, False, False),  # default mode: no cutoff, kept
        (IngestedLite(at=old), cutoff, False, True),
        (IngestedLite(at=recent), cutoff, False, False),
        (IngestedLite(at=cutoff), cutoff, False, False),  # exactly at the cutoff: kept
        (IngestedLite(at=old), cutoff, True, False),  # archived project: compressed, kept
        (IngestedLite(at=old, linked_to_open_task=True), cutoff, False, False),
        (IngestedLite(at=old, linked_to_open_task=True), cutoff, True, False),
        (IngestedLite(at=recent, linked_to_open_task=True), cutoff, False, False),
    ]
    for item, cut, archived, purged in table:
        assert purge_candidates(item, cut, archived) is purged, (item, cut, archived)
