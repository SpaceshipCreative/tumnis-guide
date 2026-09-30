"""The deploy status rules (P2-14, FR-12.2): which deployment is the project card's last
one, and which preview URLs it lists."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

T0 = datetime(2026, 3, 9, 12, 0, tzinfo=UTC)


def _dep(n: int, status: str, *, pr: int = 0, hours_ago: float, done: bool = True) -> Any:
    from tumnis.modules.coolify.rules import DeploymentView  # noqa: PLC0415

    created = T0 - timedelta(hours=hours_ago)
    return DeploymentView(
        deployment_uuid=f"dpl{n}",
        pull_request_id=pr,
        status=status,
        commit=f"{n:040x}",
        commit_message=f"change {n}",
        created_at=created,
        finished_at=created + timedelta(minutes=3) if done else None,
    )


def _app(
    fqdn: str | None = "https://portal.example.org,https://www.portal.example.org",
    template: str | None = "{{pr_id}}.{{domain}}",
) -> Any:
    from tumnis.modules.coolify.rules import ApplicationView  # noqa: PLC0415

    return ApplicationView(
        uuid="dune0portal0example00004",
        name="dune-portal",
        fqdn=fqdn,
        status="running:healthy",
        preview_url_template=template,
    )


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
def test_latest_and_previews() -> None:
    """T-P2-14-04
    latest_deployment: the newest deployment that is not a preview (pull_request_id 0),
    whatever its status (a failed or running latest is the latest), in any input order;
    None with no deployments or only previews.
    preview_urls: one link per open PR that has a preview deployment, from that PR's newest
    deployment, with the URL built from the preview template and the first domain (scheme
    and path kept, port dropped, as Coolify builds it); closed PRs' previews are hidden, an
    open PR without a preview has no link, and no URL can be built without a domain or
    from a `{{random}}` template. Links are in PR number order.
    """
    from tumnis.modules.coolify.rules import (  # noqa: PLC0415
        latest_deployment,
        preview_urls,
    )

    finished = _dep(5, "finished", hours_ago=1)
    failed = _dep(6, "failed", hours_ago=0.5)
    running = _dep(7, "in_progress", hours_ago=0.1, done=False)
    preview_new = _dep(8, "finished", pr=14, hours_ago=0.05)
    older = _dep(1, "finished", hours_ago=30)

    # latest_deployment
    assert latest_deployment([older, finished, preview_new]) == finished
    assert latest_deployment([finished, failed, older]) == failed  # failed latest
    assert latest_deployment([running, failed, finished]) == running
    assert latest_deployment([]) is None  # no deployments
    assert latest_deployment([preview_new]) is None  # only previews

    # preview_urls
    pr14_old = _dep(10, "failed", pr=14, hours_ago=20)
    pr14_new = _dep(11, "in_progress", pr=14, hours_ago=0.2, done=False)
    pr11 = _dep(12, "finished", pr=11, hours_ago=24)  # PR 11 is closed
    pr9 = _dep(13, "finished", pr=9, hours_ago=40)
    deps = [finished, pr14_old, pr11, pr14_new, pr9]

    links = preview_urls(_app(), deps, {14, 9, 21})  # PR 21 is open with no preview
    assert [(link.pull_request_id, link.url, link.status) for link in links] == [
        (9, "https://9.portal.example.org", "finished"),
        (14, "https://14.portal.example.org", "in_progress"),
    ]
    assert links[1].commit == pr14_new.commit
    assert links[1].finished_at is None
    assert links[0].finished_at == pr9.finished_at

    assert preview_urls(_app(), deps, set()) == []  # every PR closed
    assert preview_urls(_app(), [], {14}) == []  # no deployments
    assert preview_urls(_app(fqdn=None), deps, {14}) == []  # no domain
    assert preview_urls(_app(template="{{random}}.{{domain}}"), deps, {14}) == []

    custom = _app(fqdn="http://app.example.com:8443/portal", template="pr-{{pr_id}}.{{domain}}")
    (link,) = preview_urls(custom, deps, {11})
    assert link.url == "http://pr-11.app.example.com/portal"
