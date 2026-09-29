"""A stand-in for another module queueing human decisions (P0-18, R-03). No assertions live
here. Like any module, it imports `tumnis.modules.tasks.api` only: the review kind registry
and `add_review_item` are the whole interface.

- `ConflictPayload`: the payload of its `test_conflict` kind (a file changed on both sides).
- `register()`: registers the kind (a second call raises, as a duplicate kind must).
"""

from __future__ import annotations

from pydantic import BaseModel

from tumnis.modules.tasks import api as tasks

KIND = "test_conflict"


class ConflictPayload(BaseModel):
    path: str


def register() -> None:
    tasks.register_review_kind(
        tasks.ReviewKindSpec(
            kind=KIND,
            owner_module="testmod",
            payload_schema=ConflictPayload,
            actions=("accept", "reject"),
            impact_scope="project",
        )
    )
