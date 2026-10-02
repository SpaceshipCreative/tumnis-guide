"""The push integration tests' world (P4-05): `push`, the workspace's user signed in on the
in-process app, the in-process worker (`dbos`) and one fake push service that every
delivery in the process reaches (`workflows.use`), with short retry waits."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

import pytest

from tumnis.modules.notifications.tests.integration._push import PushWorld

if TYPE_CHECKING:
    from tests._auth import SessionClient
    from tests._pg import DbUrls
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock

RETRY_DELAYS_S = (0.05, 0.05)  # three attempts, a moment apart


@pytest.fixture
def push(  # noqa: PLR0917  # the fixtures the world stands on
    dbos: Any,
    workspace: WorkspaceHandle,
    clock: FixedClock,
    db: DbUrls,
    dbos_sys_db: DbUrls,
    session_client: SessionClient,
) -> Iterator[PushWorld]:
    from tumnis.core import db as core_db  # noqa: PLC0415
    from tumnis.modules.notifications import workflows  # noqa: PLC0415
    from tumnis.modules.notifications.adapters.webpush.fake import FakeWebPush  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    fake = FakeWebPush()
    workflows.use(lambda _workspace_id, _vapid: fake, retry_delays_s=RETRY_DELAYS_S)
    yield PushWorld(workspace, clock, db, dbos_sys_db, session_client, fake)
    workflows.use()  # production defaults for the next test
