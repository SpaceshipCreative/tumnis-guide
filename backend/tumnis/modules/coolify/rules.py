"""coolify pure rules (P2-14, FR-12.2): which deployment is a project card's last one, and
which preview URLs it lists. No I/O; the views are what the read-only adapter returns.

Coolify marks a pull request preview deployment with its PR number in `pull_request_id`
(0 for the application's own branch). A preview's URL is built the way Coolify builds it
(`ApplicationPreview::generatedPreviewDomain`): the application's first domain, its host
put into the preview URL template (`{{pr_id}}.{{domain}}` by default), scheme and path
kept, port dropped.
"""

import re
from collections.abc import Sequence
from typing import Final

from pydantic import AwareDatetime, BaseModel


class ApplicationView(BaseModel):
    """What the card needs of `GET /api/v1/applications/{uuid}`."""

    uuid: str
    name: str
    fqdn: str | None = None  # comma-separated URLs; the first one is the preview source
    status: str | None = None  # "running:healthy", "exited:unhealthy", ...
    preview_url_template: str | None = None
    git_repository: str | None = None
    git_branch: str | None = None


class DeploymentView(BaseModel):
    """One entry of `GET /api/v1/deployments/applications/{uuid}` (newest first)."""

    deployment_uuid: str
    pull_request_id: int = 0  # 0: the application's own branch; else a PR preview
    status: str  # queued, in_progress, finished, failed, cancelled-by-user
    commit: str | None = None  # a SHA, or "HEAD" when deployed without one
    commit_message: str | None = None
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None  # None while queued or running


class PreviewLink(BaseModel):
    """An open pull request's preview: its newest preview deployment and its URL."""

    pull_request_id: int
    url: str
    status: str
    commit: str | None = None
    finished_at: AwareDatetime | None = None


_URL: Final = re.compile(
    r"^(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*)://(?P<host>[^/:?#]+)(?::\d+)?(?P<path>/[^?#]*)?"
)


def _newest(deps: Sequence[DeploymentView]) -> DeploymentView | None:
    # Coolify lists newest first; on equal times the first listed wins.
    return max(deps, key=lambda dep: dep.created_at, default=None)


def latest_deployment(deps: Sequence[DeploymentView]) -> DeploymentView | None:
    """The newest deployment that is not a preview, whatever its status."""
    return _newest([dep for dep in deps if dep.pull_request_id == 0])


def preview_prs(deps: Sequence[DeploymentView]) -> set[int]:
    """Every PR with a preview deployment in `deps`: the open PRs to assume when GitHub
    cannot say which are open (P2-14 plan note)."""
    return {dep.pull_request_id for dep in deps if dep.pull_request_id > 0}


def preview_url(app: ApplicationView, pull_request_id: int) -> str | None:
    """The preview URL of a PR, or None when Coolify's cannot be known: no domain, no
    template, or a `{{random}}` part (Coolify draws it once and stores it)."""
    template = app.preview_url_template
    if not app.fqdn or not template or "{{random}}" in template:
        return None
    match = _URL.match(app.fqdn.split(",")[0].strip())
    if match is None:
        return None
    host = template.replace("{{domain}}", match["host"]).replace("{{pr_id}}", str(pull_request_id))
    path = match["path"] or ""
    return f"{match['scheme']}://{host}{'' if path == '/' else path}"


def preview_urls(
    app: ApplicationView, deps: Sequence[DeploymentView], open_prs: set[int]
) -> list[PreviewLink]:
    """One link per open PR with a preview deployment, from its newest one, in PR number
    order; closed PRs' previews are hidden."""
    links: list[PreviewLink] = []
    for pr in sorted(pr for pr in open_prs if pr > 0):  # 0 is no pull request
        newest = _newest([dep for dep in deps if dep.pull_request_id == pr])
        url = preview_url(app, pr) if newest is not None else None
        if newest is None or url is None:
            continue
        links.append(
            PreviewLink(
                pull_request_id=pr,
                url=url,
                status=newest.status,
                commit=newest.commit,
                finished_at=newest.finished_at,
            )
        )
    return links
