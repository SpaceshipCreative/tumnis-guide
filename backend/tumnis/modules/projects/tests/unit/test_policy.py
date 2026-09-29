"""The default approval policy of a new project (P0-17, FR-5.6, SAF-5)."""

from __future__ import annotations

import pytest


@pytest.mark.req("FR-5.6")
@pytest.mark.wp("P0-17")
def test_default_policy_matches_fr_5_6() -> None:
    """T-P0-17-16
    The gated list holds email sends, main-branch pushes and merges, force pushes,
    production deploys, destructive Proxmox changes, spending and file deletion; the
    allowed list holds feature-branch pushes, pull requests, preview deploys, creating and
    starting guests, drafts and reads. No action is in both.
    """
    from tumnis.modules.projects.rules import ALLOWED_DEFAULT, GATED_DEFAULT  # noqa: PLC0415

    assert GATED_DEFAULT == (
        "send_email",
        "push_main",
        "merge_main",
        "force_push",
        "deploy_production",
        "proxmox_delete_guest",
        "proxmox_rollback_snapshot",
        "proxmox_storage_change",
        "proxmox_network_change",
        "spend_money",
        "delete_files",
    )
    assert ALLOWED_DEFAULT == (
        "push_feature_branch",
        "open_pull_request",
        "trigger_preview_deploy",
        "proxmox_create_guest",
        "proxmox_start_guest",
        "create_draft",
        "read",
    )
    assert not set(GATED_DEFAULT) & set(ALLOWED_DEFAULT)
