"""Provisioning ids and the template version (P1-06, FR-5.10): the server names the
template of the daemon's own release, and every id a replayed step computes is the same."""

from pathlib import Path
from uuid import UUID

import pytest

from tumnis.modules.agents import api

REPO = Path(__file__).resolve().parents[6]
PROJECT = UUID("0b6f1a9e-5a7e-4c0e-9d2a-6c1f3e8b2a10")


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
def test_template_version_is_the_template_in_the_repo() -> None:
    """The version sent in `provision` is profiles/project-template/VERSION."""
    version = (REPO / "profiles" / api.TEMPLATE_NAME / "VERSION").read_text().strip()
    assert version == api.TEMPLATE_VERSION


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
def test_provision_ids_are_deterministic() -> None:
    """Workflow ids per attempt, the project read back from them, and request and message
    ids derived from the workflow id alone."""
    first = api.provision_workflow_id(PROJECT)
    retry = api.provision_workflow_id(PROJECT, 2)
    assert first == f"provision:{PROJECT}"
    assert retry == f"provision:{PROJECT}:2"
    assert api.project_of_provision(first) == PROJECT
    assert api.project_of_provision(retry) == PROJECT
    assert api.project_of_provision(f"run_skill:{PROJECT}") is None
    assert api.project_of_provision("provision:not-a-uuid") is None
    assert api.provision_topic(PROJECT) == first
    assert api.provision_request_id(first) == api.provision_request_id(first)
    assert api.provision_request_id(first) != api.provision_request_id(retry)
    request = api.provision_request_id(first)
    assert api.provision_message_id(request) == api.provision_message_id(request)
    assert api.provision_message_id(request) != request


@pytest.mark.req("FR-5.10")
@pytest.mark.wp("P1-06")
def test_provision_timeout_is_configurable() -> None:
    """300 s by default (plan), set by the worker from settings; never zero."""
    before = api.provision_timeout_s()
    try:
        api.configure_provisioning(timeout_s=7)
        assert api.provision_timeout_s() == 7
        with pytest.raises(ValueError, match="positive"):
            api.configure_provisioning(timeout_s=0)
    finally:
        api.configure_provisioning(timeout_s=before)
    assert api.PROVISION_TIMEOUT_S_DEFAULT == 300
