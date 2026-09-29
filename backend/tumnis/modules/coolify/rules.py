"""coolify pure rules (P2-14, FR-12.2): which deployment is a project card's last one, and
which preview URLs it lists. No I/O; the views are what the read-only adapter returns.

Coolify marks a pull request preview deployment with its PR number in `pull_request_id`
(0 for the application's own branch). A preview's URL is built the way Coolify builds it
(`ApplicationPreview::generatedPreviewDomain`): the application's first domain, its host
put into the preview URL template (`{{pr_id}}.{{domain}}` by default), scheme and path
kept, port dropped.
"""

from collections.abc import Sequence

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


def latest_deployment(deps: Sequence[DeploymentView]) -> DeploymentView | None:
    """The newest deployment that is not a preview, whatever its status."""
    raise NotImplementedError


def preview_urls(
    app: ApplicationView, deps: Sequence[DeploymentView], open_prs: set[int]
) -> list[PreviewLink]:
    """One link per open PR with a preview deployment, in PR number order."""
    raise NotImplementedError
