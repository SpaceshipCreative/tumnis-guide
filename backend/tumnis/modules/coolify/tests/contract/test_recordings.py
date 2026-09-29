"""The recorded Coolify answers (P2-14, FR-12.2): the real read-only client reads each
recorded application and its deployments, and the rules turn them into the project card's
last deployment (status, time, commit) and the preview URLs of open pull requests."""

from __future__ import annotations

import pytest

from tumnis.modules.coolify.tests.replay import BASE_URL, TOKEN, Replay, load, recording_names

pytestmark = pytest.mark.contract


@pytest.mark.req("FR-12.2")
@pytest.mark.wp("P2-14")
@pytest.mark.xfail(strict=True, reason="spec:P2-14")
@pytest.mark.parametrize("recording", recording_names())
async def test_last_deployment_and_preview_urls(recording: str) -> None:
    """T-P2-14-01
    Given a recording (successful, failed or in-progress deploy; two previews with one PR
    closed), the real client over the replayed HTTP answers gives the application and its
    ten newest deployments with two GETs carrying the bearer token; `latest_deployment` is
    the recorded last deployment (status, created and finished time, commit) and
    `preview_urls` lists exactly the recorded preview links of the open PRs.
    """
    from tumnis.modules.coolify.rules import latest_deployment, preview_urls  # noqa: PLC0415
    from tumnis.modules.coolify.tests.replay import recorded_api  # noqa: PLC0415

    rec = load(recording)
    assert rec["notes"].startswith("Scrubbed:")
    replay = Replay(rec)
    subject = recorded_api(replay)

    app = await subject.get_application(rec["app_uuid"])
    deployments = await subject.list_deployments(rec["app_uuid"])

    assert app.uuid == rec["app_uuid"]
    assert app.name == rec["responses"]["application"]["name"]
    assert len(deployments) == len(rec["responses"]["deployments"]["deployments"])
    assert [(r.method, r.url.path, dict(r.url.params)) for r in replay.requests] == [
        ("GET", f"/api/v1/applications/{rec['app_uuid']}", {}),
        ("GET", f"/api/v1/deployments/applications/{rec['app_uuid']}", {"take": "10"}),
    ]
    for request in replay.requests:
        assert str(request.url).startswith(BASE_URL)
        assert request.headers["authorization"] == f"Bearer {TOKEN}"

    latest = latest_deployment(deployments)
    expected = rec["expected"]["latest"]
    if expected is None:
        assert latest is None
    else:
        assert latest is not None
        assert latest.model_dump(mode="json", include=set(expected)) == expected

    links = preview_urls(app, deployments, set(rec["open_prs"]))
    assert [link.model_dump(mode="json") for link in links] == rec["expected"]["previews"]
