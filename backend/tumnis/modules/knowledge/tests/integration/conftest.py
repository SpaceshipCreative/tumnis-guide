"""Fixtures for the knowledge integration tests (P1-14): the core database pointed at the
test database with a master key loaded (location credentials are sealed with the
workspace data key), and knowledge's `project.created` subscribers registered."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests._pg import DbUrls
    from tests.fixtures import MasterKeyFile, WorkspaceHandle


@pytest.fixture
def knowledge_ws(
    db: DbUrls, workspace: WorkspaceHandle, master_key_file: MasterKeyFile
) -> WorkspaceHandle:
    import tumnis.modules.knowledge.events  # noqa: F401, PLC0415  # registers the subscribers
    from tumnis.core import db as core_db  # noqa: PLC0415

    core_db.configure(app_url=db.app, direct_url=db.app, owner_url=db.owner, pooled=False)
    return workspace
