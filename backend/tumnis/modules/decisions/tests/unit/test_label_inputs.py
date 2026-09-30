"""The label request's inputs (P1-07, Data flow rule 6): only the five whitelisted fields
of a task and its context ever reach the decisions provider."""

from __future__ import annotations

import uuid

import pytest

WHITELIST = {"title", "parent_title", "project_name", "project_goal", "reserved_judgments"}


@pytest.mark.req("Data flow rule 6")
@pytest.mark.wp("P1-07")
@pytest.mark.xfail(strict=True, reason="spec:P1-07")
def test_label_inputs_are_whitelisted() -> None:
    """T-P1-07-14
    A task with a description (first action, acceptance criteria), comments and links, in
    a project with a client and a brief, gives only `title`, `parent_title`,
    `project_name`, `project_goal` and `reserved_judgments`; none of the other text
    appears in any value.
    """
    from tumnis.modules.decisions.catalog import DecisionPoint, build_request  # noqa: PLC0415
    from tumnis.modules.decisions.rules import (  # type: ignore[attr-defined]  # noqa: PLC0415
        label_inputs,
    )

    task = {
        "id": str(uuid.uuid4()),
        "title": "Send Acme the March invoice",
        "first_action": "Open the invoice template SECRET-FIRST-ACTION",
        "acceptance_criteria": "Acme confirms receipt SECRET-CRITERIA",
        "comments": ["Dana said to add the late fee SECRET-COMMENT"],
        "links": [{"url": "https://example.com/SECRET-LINK"}],
        "estimate_minutes": 20,
    }
    project = {
        "name": "Acme site",
        "goal": "Ship the Acme marketing site redesign by June",
        "client": "Acme SECRET-CLIENT",
        "brief_md": "SECRET-BRIEF",
    }
    inputs = label_inputs(
        task,
        parent_title="Monthly billing",
        project=project,
        reserved_judgments=["pricing", "hiring"],
    )

    assert set(inputs) == WHITELIST
    assert inputs == {
        "title": "Send Acme the March invoice",
        "parent_title": "Monthly billing",
        "project_name": "Acme site",
        "project_goal": "Ship the Acme marketing site redesign by June",
        "reserved_judgments": ["pricing", "hiring"],
    }
    assert "SECRET" not in repr(inputs)
    req = build_request(DecisionPoint.QUICK_ADD_LABEL, inputs)
    assert set(req.fields_sent) == WHITELIST

    bare = label_inputs(task, parent_title=None, project=None, reserved_judgments=[])
    assert bare == {"title": "Send Acme the March invoice"}
