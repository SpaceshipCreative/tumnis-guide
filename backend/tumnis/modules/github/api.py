"""github public functions and DTOs; the only file other modules may import (P2-13, FR-12.1).

Tumnis reads pull requests, never writes them: the worker reads GitHub with the workspace's
own read-only token (`adapters/github_status.py`), the api only stores and enqueues
(architecture principle 3). A pull request is an integrations Artifact (`kind
pull_request`, external id `owner/repo#number`, `state` open, merged or closed, and
`checks.pr_status` for the card); the last read's answers and ETags are kept as the
artifact's raw payload, so the next read is conditional.

- `track_pull_request`: a pull request URL becomes an artifact, if it is a github.com pull
  request in a repository the workspace allows (Settings > GitHub).
- `pull_requests`, `request_refresh`: what a task card shows, and the refresh a stale card
  asks for (one per artifact while another is running: deduplication id `refresh:<id>`).
- `refresh_artifact`: the worker's read and write (the workflow calls it).
- `accept_webhook`: a signed delivery, for when a relay forwards GitHub's (SEC-5).
"""

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Any, Final, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tumnis.core import deadletter, modules
from tumnis.core.adapters.errors import AdapterRejected
from tumnis.core.live import mark_changed
from tumnis.core.outbox import emit
from tumnis.core.settings_store import SettingSection, get_setting, register_section
from tumnis.core.tenancy import WorkspaceContext, session_for, tenant_session
from tumnis.core.types import SYSTEM_ACTOR
from tumnis.modules.github.adapters.port import Fetched, GitHubStatus
from tumnis.modules.github.models import WebhookDelivery
from tumnis.modules.github.payloads import GitHubFetchedV1
from tumnis.modules.github.rules import (
    CheckRunView,
    CheckState,
    PrRef,
    PrState,
    PullView,
    ReviewSummary,
    ReviewView,
    Snapshot,
    StatusView,
    allowed_repo,
    parse_pr_url,
    pr_state,
    pr_status,
)
from tumnis.modules.github.signature import verify_signature
from tumnis.modules.integrations import api as integrations

SETTINGS_SECTION: Final = "github"
PROVIDER: Final = "github"
ACCOUNT: Final = "api.github.com"
KIND: Final = "pull_request"
RAW_RECORD_TYPE: Final = "artifact"
QUEUE: Final = "github"  # its own DBOS queue: limiters are per queue (A9)
REFRESH_WORKFLOW: Final = "github_refresh_artifact"
FRESH_FOR: Final = timedelta(seconds=60)  # plan default: a status read this recently is fresh
TASK_ENTITY: Final = "task"  # the live channel's name for a task (tasks.api.LIVE_ENTITY)

_deliveries: Table = WebhookDelivery.__table__  # type: ignore[assignment]

RepoEntry = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9-]{1,39}/([A-Za-z0-9._-]{1,100}|\*)$")
]


class GitHubSettings(BaseModel):
    """Settings > GitHub: Tumnis's own read-only fine-grained token, the repositories it may
    read (`owner/repo` or `owner/*`; empty allows none) and the webhook secret (empty leaves
    the webhook endpoint off)."""

    token: str = ""
    allowed_repos: Annotated[list[RepoEntry], Field(max_length=200)] = Field(default_factory=list)
    webhook_secret: str = ""


register_section(
    SettingSection(
        SETTINGS_SECTION, GitHubSettings, secret_fields=frozenset({"token", "webhook_secret"})
    )
)


class NotAPullRequest(ValueError):  # noqa: N818  # reads as the condition it reports
    """The URL is not a github.com pull request."""


class RepoNotAllowed(ValueError):  # noqa: N818
    """The pull request's repository is not on the workspace's allow-list."""


# --- Reading a pull request --------------------------------------------------------------------


@dataclass(frozen=True)
class Fetch:
    """One pass over a pull request: what was read, how many requests it took and how many of
    them GitHub answered 304 Not Modified."""

    snapshot: Snapshot
    requests: int
    not_modified: int


async def _read[T](
    call: Callable[[str | None], Awaitable[Fetched[T]]], etag: str | None, held: T | None
) -> tuple[T, str | None, bool]:
    """A conditional read: sends the ETag only while `held` (what it stands for) is in hand,
    so a 304 always has something to keep."""
    sent = etag if held is not None else None
    read = await call(sent)
    if read.value is not None:
        return read.value, read.etag, False
    if held is None:  # a 304 to a read that sent no ETag: not GitHub's contract
        raise AdapterRejected("github.status", "read", "unexpected 304")
    return held, read.etag or sent, True


async def fetch_pull_request(status: GitHubStatus, ref: PrRef, previous: Snapshot | None) -> Fetch:
    """Reads the pull request, the commit statuses and check runs of its head commit and its
    reviews (four requests). Each read is conditional on the ETag `previous` holds, except
    the statuses and check runs after the head commit changed; a 304 keeps the previous
    part. GitHub errors propagate as adapter errors."""
    etags: dict[str, str] = {}
    not_modified = 0

    def keep(name: str, etag: str | None) -> None:
        if etag is not None:
            etags[name] = etag

    old = previous.etags if previous is not None else {}
    pull, etag, cached = await _read(
        lambda e: status.get_pull(ref.owner, ref.repo, ref.number, e),
        old.get("pull"),
        previous.pull if previous is not None else None,
    )
    keep("pull", etag)
    not_modified += cached
    same_head = previous is not None and previous.pull.head.sha == pull.head.sha
    sha = pull.head.sha
    statuses, etag, cached = await _read(
        lambda e: status.get_combined_status(ref.owner, ref.repo, sha, e),
        old.get("status"),
        previous.statuses if previous is not None and same_head else None,
    )
    keep("status", etag)
    not_modified += cached
    runs, etag, cached = await _read(
        lambda e: status.list_check_runs(ref.owner, ref.repo, sha, e),
        old.get("check_runs"),
        previous.check_runs if previous is not None and same_head else None,
    )
    keep("check_runs", etag)
    not_modified += cached
    reviews, etag, cached = await _read(
        lambda e: status.list_reviews(ref.owner, ref.repo, ref.number, e),
        old.get("reviews"),
        previous.reviews if previous is not None else None,
    )
    keep("reviews", etag)
    not_modified += cached
    snapshot = Snapshot(pull=pull, statuses=statuses, check_runs=runs, reviews=reviews, etags=etags)
    return Fetch(snapshot=snapshot, requests=4, not_modified=not_modified)


def artifact_record(
    ref: PrRef, snapshot: Snapshot, fetched_at: datetime
) -> integrations.ArtifactRecord:
    """The pull request as the canonical Artifact: the canonical URL, its state and the
    card's `checks.pr_status`."""
    return integrations.ArtifactRecord(
        external_id=ref.external_id,
        provider_url=ref.url,
        fetched_at=fetched_at,
        kind=KIND,
        url=ref.url,
        state=pr_state(snapshot.pull),
        checks={"pr_status": pr_status(ref, snapshot)},
    )


# --- What a task card shows ---------------------------------------------------------------------


class PullRequestOut(BaseModel):
    """A tracked pull request as the card shows it; every status field is None until the
    first read."""

    artifact_id: UUID
    url: str
    repo: str
    number: int
    title: str | None
    state: PrState | None
    draft: bool
    checks: CheckState | None
    review: ReviewSummary | None
    checked_at: datetime | None


def pull_request_ref(external_id: str) -> PrRef | None:
    """The `owner/repo#number` an artifact of this module carries, as a PrRef."""
    name, _, number = external_id.rpartition("#")
    owner, _, repo = name.partition("/")
    if not (owner and repo and number.isdigit() and int(number) >= 1):
        return None
    return PrRef(owner=owner, repo=repo, number=int(number))


def pull_request_key(url: str) -> str | None:
    """The stable key (`owner/repo#number`, lower case) of a github.com pull request URL,
    whatever follows the number; None for any other URL. Two links to one pull request
    have one key."""
    parsed = parse_pr_url(url)
    return (
        None
        if parsed is None
        else PrRef(owner=parsed[0], repo=parsed[1], number=parsed[2]).external_id
    )


def _out(artifact: integrations.ArtifactOut) -> PullRequestOut | None:
    ref = pull_request_ref(artifact.external_id)
    if ref is None or artifact.kind != KIND:
        return None
    read = artifact.state is not None  # a stored artifact nobody has read has no status
    status: dict[str, Any] = artifact.checks.get("pr_status", {}) if read else {}
    return PullRequestOut(
        artifact_id=artifact.id,
        url=artifact.url or ref.url,
        repo=ref.full_name,
        number=ref.number,
        title=status.get("title"),
        state=artifact.state if read else None,
        draft=bool(status.get("draft", False)),
        checks=status.get("checks"),
        review=status.get("review"),
        checked_at=artifact.fetched_at if read else None,
    )


async def _settings(ctx: WorkspaceContext) -> GitHubSettings:
    found = await get_setting(ctx, SETTINGS_SECTION, GitHubSettings)
    return found.value if found is not None else GitHubSettings()


async def track_pull_request(
    ctx: WorkspaceContext,
    url: str,
    *,
    now: datetime,
    session: AsyncSession | None = None,
) -> PullRequestOut:
    """Stores the pull request as an artifact (nothing is read yet). NotAPullRequest for a
    URL that is not a github.com pull request; RepoNotAllowed outside the allow-list. In
    both cases nothing is written and GitHub is not called."""
    parsed = parse_pr_url(url)
    if parsed is None:
        raise NotAPullRequest(url)
    ref = PrRef(owner=parsed[0], repo=parsed[1], number=parsed[2])
    settings = await _settings(ctx)
    if not allowed_repo(ref.owner, ref.repo, settings.allowed_repos):
        raise RepoNotAllowed(ref.full_name)
    async with session_for(ctx, session) as s:
        connection_id = await integrations.upsert_connection(
            ctx, kind="code", provider=PROVIDER, account=ACCOUNT, session=s
        )
        artifact = await integrations.upsert_artifact(
            ctx,
            connection_id=connection_id,
            kind=KIND,
            external_id=ref.external_id,
            url=ref.url,
            now=now,
            session=s,
        )
    out = _out(artifact)
    assert out is not None  # noqa: S101  # built from a parsed reference
    return out


async def pull_requests(
    ctx: WorkspaceContext, artifact_ids: Sequence[UUID], *, session: AsyncSession | None = None
) -> list[PullRequestOut]:
    """The stored status of these artifacts (the pull requests among them), in order."""
    found = await integrations.get_artifacts(ctx, artifact_ids, session=session)
    return [out for artifact in found if (out := _out(artifact)) is not None]


def is_stale(pr: PullRequestOut, now: datetime) -> bool:
    return pr.checked_at is None or now - pr.checked_at >= FRESH_FOR


async def enqueue_refresh(ctx: WorkspaceContext, artifact_id: UUID) -> None:
    """Queue a read of the pull request for the worker, unless one is already queued or
    running for it (deduplication id `refresh:<artifact_id>`). Makes no outbound call."""
    await deadletter.dbos_client().enqueue_async(
        {
            "queue_name": QUEUE,
            "workflow_name": REFRESH_WORKFLOW,
            "deduplication_id": f"refresh:{artifact_id}",
            "duplication_policy": "return-existing",
        },
        str(ctx.workspace_id),
        str(artifact_id),
    )


async def request_refresh(ctx: WorkspaceContext, pr: PullRequestOut, *, now: datetime) -> bool:
    """Opening a card asks for a fresh read: queued when the status was never read or is
    older than `FRESH_FOR`, and while the module is on for the workspace. Returns whether a
    refresh was requested."""
    if not is_stale(pr, now) or not await modules.enabled("github", ctx.workspace_id):
        return False
    await enqueue_refresh(ctx, pr.artifact_id)
    return True


# --- The worker's read ---------------------------------------------------------------------

Refreshed = Literal["refreshed", "skipped", "not_allowed", "gone", "rejected"]
StatusFor = Callable[[GitHubSettings], GitHubStatus | None]


async def refresh_artifact(
    ctx: WorkspaceContext, artifact_id: UUID, status_for: StatusFor, *, now: datetime
) -> Refreshed:
    """Reads GitHub for one pull request artifact and stores what it said. `status_for`
    builds the client from the settings (None: nothing to read with, e.g. no token). A
    repository that left the allow-list is not read. The status, the raw answers (with their
    ETags) and a `github.fetched` event are written in one transaction, which also marks the
    linked tasks changed for the live channel when the state or checks changed and
    `artifact.updated` went out. An answer GitHub refuses (the pull request is gone, the
    token cannot see it) changes nothing; an outage raises for the workflow to retry."""
    found = await integrations.get_artifacts(ctx, [artifact_id])
    ref = pull_request_ref(found[0].external_id) if found else None
    if not found or ref is None or found[0].kind != KIND:
        return "gone"
    artifact = found[0]
    settings = await _settings(ctx)
    if not allowed_repo(ref.owner, ref.repo, settings.allowed_repos):
        return "not_allowed"
    status = status_for(settings)
    if status is None:
        return "skipped"
    stored = await integrations.get_raw_payload(
        ctx, artifact.connection_id, RAW_RECORD_TYPE, artifact.external_id
    )
    previous = _snapshot(stored)
    try:
        fetch = await fetch_pull_request(status, ref, previous)
    except AdapterRejected:
        return "rejected"
    record = artifact_record(ref, fetch.snapshot, now)
    async with tenant_session(ctx) as s:
        changed = await integrations.set_artifact_status(
            ctx, artifact.id, state=record.state, checks=record.checks, fetched_at=now, session=s
        )
        await integrations.store_raw_payloads(
            ctx,
            artifact.connection_id,
            [
                integrations.RawItem(
                    external_id=artifact.external_id,
                    record_type=RAW_RECORD_TYPE,
                    payload=fetch.snapshot.model_dump(mode="json"),
                    fetched_at=now,
                )
            ],
            session=s,
        )
        await emit(
            s,
            GitHubFetchedV1(requests=fetch.requests, not_modified=fetch.not_modified),
            occurred_at=now,
        )
        if changed:
            owners = await integrations.context_owners(
                ctx, target_type="artifact", target_id=artifact.id, owner_type="task", session=s
            )
            for owner in owners:
                mark_changed(s, TASK_ENTITY, owner)
    return "refreshed"


def _snapshot(stored: dict[str, Any] | None) -> Snapshot | None:
    if stored is None:
        return None
    try:
        return Snapshot.model_validate(stored)
    except ValidationError:
        return None


async def pollable(ctx: WorkspaceContext) -> list[UUID]:
    """The open pull request artifacts the poll refreshes: never read or still open, in a
    repository the allow-list still names (merged and closed ones are left alone)."""
    settings = await _settings(ctx)
    if not settings.allowed_repos:
        return []
    found = await integrations.list_artifacts(ctx, kind=KIND, open_only=True)
    ids: list[UUID] = []
    for artifact in found:
        ref = pull_request_ref(artifact.external_id)
        if ref is not None and allowed_repo(ref.owner, ref.repo, settings.allowed_repos):
            ids.append(artifact.id)
    return ids


# --- The webhook ---------------------------------------------------------------------------

WebhookOutcome = Literal["accepted", "off", "bad_signature", "no_delivery_id", "duplicate"]
_EVENTS_WITH_PULLS: Final = {"pull_request", "check_run", "check_suite"}


def _webhook_refs(event: str, payload: dict[str, Any]) -> list[PrRef]:
    """The pull requests a delivery is about: the one in a `pull_request` payload, or those a
    check run or suite names (with the repository they are in)."""
    if event not in _EVENTS_WITH_PULLS:
        return []
    refs: list[PrRef] = []
    if event == "pull_request":
        pull = payload.get("pull_request")
        parsed = parse_pr_url(str(pull.get("html_url", ""))) if isinstance(pull, dict) else None
        if parsed is not None:
            refs.append(PrRef(owner=parsed[0], repo=parsed[1], number=parsed[2]))
        return refs
    check = payload.get(event)
    repository = payload.get("repository")
    full_name = str(repository.get("full_name", "")) if isinstance(repository, dict) else ""
    owner, _, repo = full_name.partition("/")
    pulls = check.get("pull_requests") if isinstance(check, dict) else None
    if owner and repo and isinstance(pulls, list):
        for pull in pulls:
            number = pull.get("number") if isinstance(pull, dict) else None
            if isinstance(number, int) and number >= 1:
                refs.append(PrRef(owner=owner, repo=repo, number=number))
    return refs


async def accept_webhook(  # a delivery's parts
    workspace_id: UUID,
    *,
    body: bytes,
    signature: str | None,
    delivery_id: str | None,
    event: str | None,
    now: datetime,
) -> WebhookOutcome:
    """A signed delivery for one workspace. `off` while the module is off for it or no
    webhook secret is set (the route answers 404: nothing is there); `bad_signature` when
    `X-Hub-Signature-256` does not match the raw body (nothing is stored);
    `no_delivery_id` without `X-GitHub-Delivery`; `duplicate` for a delivery id seen before;
    else `accepted`, with a refresh queued for each tracked, allowed pull request the payload
    names. The delivery is recorded and the refreshes queued before the commit, so a failure
    leaves it unrecorded and GitHub's redelivery is not refused."""
    ctx = WorkspaceContext(workspace_id, SYSTEM_ACTOR)
    if not await modules.enabled("github", workspace_id):
        return "off"
    settings = await _settings(ctx)
    if not settings.webhook_secret:
        return "off"
    if not verify_signature(settings.webhook_secret.encode(), body, signature):
        return "bad_signature"
    if not delivery_id or len(delivery_id) > 200:  # noqa: PLR2004  # the column's check
        return "no_delivery_id"
    async with tenant_session(ctx) as s:
        inserted = (
            await s.execute(
                pg_insert(_deliveries)
                .values(delivery_id=delivery_id, received_at=now)
                .on_conflict_do_nothing(index_elements=["workspace_id", "delivery_id"])
                .returning(_deliveries.c.id)
            )
        ).first()
        if inserted is None:
            return "duplicate"
        for artifact_id in await _tracked(ctx, event or "", body, settings, s):
            await enqueue_refresh(ctx, artifact_id)
    return "accepted"


async def _tracked(
    ctx: WorkspaceContext, event: str, body: bytes, settings: GitHubSettings, s: AsyncSession
) -> list[UUID]:
    try:
        payload = json.loads(body)
    except ValueError:
        return []
    if not isinstance(payload, dict):
        return []
    refs = [
        ref
        for ref in _webhook_refs(event, payload)
        if allowed_repo(ref.owner, ref.repo, settings.allowed_repos)
    ]
    found = await integrations.find_artifacts(
        ctx, kind=KIND, external_ids=[ref.external_id for ref in refs], session=s
    )
    return [artifact.id for artifact in found]


__all__ = [
    "ACCOUNT",
    "KIND",
    "QUEUE",
    "REFRESH_WORKFLOW",
    "CheckRunView",
    "Fetch",
    "GitHubSettings",
    "NotAPullRequest",
    "PullRequestOut",
    "PullView",
    "RepoNotAllowed",
    "ReviewView",
    "StatusView",
    "accept_webhook",
    "artifact_record",
    "enqueue_refresh",
    "fetch_pull_request",
    "pollable",
    "pull_request_key",
    "pull_request_ref",
    "pull_requests",
    "refresh_artifact",
    "request_refresh",
    "track_pull_request",
]
