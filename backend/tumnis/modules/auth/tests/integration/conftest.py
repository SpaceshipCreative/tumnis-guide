"""Fixtures for the auth integration tests (P0-13); the shared ones (app, client, clock,
workspace, session_client, pepper_file) come from backend/tests/fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from fastapi import FastAPI

    from tests._auth import Account
    from tests.fixtures import WorkspaceHandle
    from tumnis.core.clock import FixedClock


@pytest.fixture
async def account(app: FastAPI, workspace: WorkspaceHandle, clock: FixedClock) -> Account:
    """The `workspace` fixture's user with a confirmed TOTP secret, ready to sign in."""
    from tests.fixtures import enroll_workspace_user  # noqa: PLC0415

    return await enroll_workspace_user(workspace, clock)
