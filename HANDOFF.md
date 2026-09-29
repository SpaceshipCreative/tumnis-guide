# HANDOFF: P2-14 · Coolify status on project cards

Branch `wp/P2-14` (pushed to origin up to the handoff commit). **No PR opened yet.**
Stopped on a coordinator "HANDOFF NOW" (context watcher).

## Commits (on top of main f256fa1)

| SHA | What |
| --- | --- |
| 2eec6cb | `test(coolify): P2-14 spec tests (red)`: T-01..T-05 as `spec:P2-14` expected failures, recordings, replay helper, interface stubs |
| 96ef346 | `feat(coolify)`: rules `latest_deployment`, `preview_urls` (T-P2-14-04 green, marker removed) |
| 63050a9 | `feat(coolify)`: read-only adapter `CoolifyStatusApi` + `ReadOnlyHttp` + `FakeCoolifyStatus`, registration `coolify.status`, fake/recorded contract suite, error-mapping unit tests (T-01, T-02, T-03 green, markers removed) |
| 7c83017 | `feat(coolify)`: revision `coolify_0001` (`deployment_status`), `api.py` (settings section `coolify`, `record_poll`, `record_failure`, `deploy_status`), `workflows.py` (`coolify_poll_tick` every 5 min, `poll_workspace`, `use()` seam), `GET /v1/coolify/status`, `projects.api.links_of_kind`, `worker.register_module_schedules` (identical text to P1-09's hook so the merge is clean), `make gen` output, LIVE_MAP entry, integration tests `tests/integration/test_poll.py` |
| (this) | `chore: P2-14 handoff` |

Alembic revision id: `coolify_0001` (new branch `coolify`, `depends_on = "auth_0001"`, no chain conflict with core).

## Spec tests

- Green, markers removed: T-P2-14-01 (4 recording cases), T-P2-14-02, T-P2-14-03, T-P2-14-04.
- Still red (`test.fails`): T-P2-14-05, `frontend/src/components/project/DeployStatus.test.tsx`. `DeployStatus.tsx` on the branch is still the spec stub (renders null); `ProjectCard` already takes a `deploy` prop but ignores it.

## Verified locally

- `make check` green at 7c83017 (935 backend unit passed; Vitest 50 passed + 1 expected fail). Semgrep meta test needs `SEMGREP_SETTINGS_FILE`, `SEMGREP_LOG_FILE`, `SEMGREP_VERSION_CACHE_PATH` under $TMPDIR.
- Coolify unit + contract: `cd backend && uv run pytest tumnis/modules/coolify -m "not integration"` all green.
- `make test-int` was started at 7c83017 in the background and had not finished at handoff: **not yet verified**; the new `tumnis/modules/coolify/tests/integration/test_poll.py` has never run. Run `make test-int` (bare) and fix what fails there first.
- Vitest under this VM's load (load avg 50 to 77) flakes on unrelated `findBy` timeouts (ProjectList, SessionsSection); a rerun passes.

## Remaining steps (TDD step 4, then PR)

1. Frontend implementation for T-P2-14-05. A near-complete WIP of `DeployStatus.tsx` is at the end of this file (not committed as code). Before using it: it imports a `problemMessage` that does not exist in `src/lib/fetch.ts`; replace with the settings pattern `error instanceof ApiError ? (error.problem.detail ?? error.message) : error.message`. It has `DeployStatus` (the card list: `ul[aria-label=Deployments]`, `li[aria-label=<name or uuid>]`, `data-testid=deploy-chip` words "Deployed <t>", "Deploy failed <t>", "Deploying since <t>", "No deploys yet"; short SHA; "Status out of date" when `error`; `PR #n preview` links with `target=_blank rel="noopener noreferrer"`) and `CoolifyApps` (rail list + link/unlink by UUID through one versioned `PATCH /v1/projects/{id}` with the project's links, keeping non-coolify links).
2. Wire the card: in `ProjectCard.tsx` render `<DeployStatus apps={deploy ?? []} timeZone={timeZone} />` (it renders nothing for an empty list); `ProjectCardGrid` takes a `deploy` map by project id; `DashboardPage` reads `deployStatusQuery()` (add to `components/dashboard/queries.ts`: `queryOptions({...coolifyListDeployStatusOptions(), retry: retryOnce})`) and passes `Object.fromEntries(data.map(e => [e.project_id, e.apps]))`; prefetch it (settled) in `routes/index.tsx`'s loader; add an MSW default `http.get("/v1/coolify/status", () => HttpResponse.json([]))` to `dashboardDefaults` in `src/test/msw/dashboard.ts` (the MSW server errors on unhandled requests).
3. Flip `test.fails` to `test` in `DeployStatus.test.tsx` once it passes (approved marker removal); add a small non-spec Vitest test for `CoolifyApps` linking (PATCH body `{links:[...], version}`) at 375 and 1280.
4. Mounting `CoolifyApps` in the Connections rail: the rail (`components/project/rail/ConnectionsSection.tsx`) exists only on P0-24's open PR #57. Either mount it there after #57 merges (summary already counts `coolify_app` links and its form keeps them), or leave it exported and say so in the PR (deviation).
5. `make check`, `make test` (unit + contract), `make test-int`, Vitest; then push and open the PR per the prompt (`gh pr create --base main --head wp/P2-14 --title "[P2-14] impl: Coolify status on project cards" --body-file <file>`, then `gh pr comment <url> --body "@coderabbitai review"`) and run the review loop. CI works again (repo public since ~23:10Z).

## Decisions and deviations (put these in the PR body)

- **No `app_links` table.** Project-to-app links stay in projects' existing `project_links` (kind `coolify_app`, P0-17; P0-24's Connections form already keeps them), read through the new `projects.api.links_of_kind`. A second link table would split the truth. The plan's A7 lists `app_links(project_id, app_uuid)`; A7 needs updating (or Scott may prefer the table).
- `deployment_status` columns: `app_uuid, app_name, status, deployment_uuid, commit, started_at, finished_at, previews jsonb, checked_at, error` (`error` in {unavailable, rejected}).
- Coolify is a plain adapter (`coolify.status`, port `CoolifyStatus` with exactly the three reads), not an `integrations` Connector: T-P2-14-02 requires the public surface to be exactly those three. No `artifact.updated` event (A8 lists coolify as an emitter, but artifacts belong to integrations and the WP section does not ask for it).
- Open PRs: the github module (P2-13) does not exist yet, so every preview in the ten newest deployments is listed (the plan's "without GitHub configured" rule; `rules.preview_prs`). `record_poll(open_prs=...)` is the seam for P2-13.
- Credentials: settings section `coolify` (`base_url`, secret `token`) through the existing `GET/PUT /v1/settings/{section}`; no dedicated Settings UI.
- Recordings are synthesized from Coolify v4.4's source and OpenAPI (`{count, deployments}` answer, `finished_at`, `pull_request_id`, preview URL from `preview_url_template` and the first fqdn), scrubbed to example.com/org, not captured from Scott's Coolify (no live access from the VM). **Scott item:** re-record against the homelab Coolify version.
- T-P2-14-01's own host assertion was corrected in 63050a9 before merge: the SSRF guard pins the request URL to the IP, so the test now checks the Host header and the pinned address (disclose in the PR).
- `ReadOnlyHttp` adds port 8000 (Coolify's default) to the net policy's ports; the worker uses `Settings().net_policy()` (self-hosted reaches the LAN).
- `worker.register_module_schedules` is byte-identical to P1-09's (PR #47) so whichever merges second merges cleanly.

## Shared-file edits so far

None of `backend/pyproject.toml`, `uv.lock`, `Makefile`, `AGENTS.md`, `.importlinter`, `frontend/package.json`. Cross-module edits: `backend/tumnis/modules/projects/api.py` (+`ProjectLinkOut`, `links_of_kind`), `backend/tumnis/worker.py`, `frontend/src/lib/live-map.ts`, `frontend/src/components/dashboard/ProjectCard.tsx` (prop), generated `schemas/openapi.json` and `frontend/src/api/*`.

## Scott items

- Re-record the Coolify fixtures against the homelab Coolify (and confirm `finished_at` exists in that version).
- Create the Coolify token with read permission only and set it at `PUT /v1/settings/coolify` (`{"values": {"base_url": "...", "token": "..."}}`).
- Decide `app_links` table vs `project_links(kind=coolify_app)`.

## Verify commands

```bash
cd backend && uv run pytest -q -p no:randomly tumnis/modules/coolify -m "not integration"
make check        # from the worktree root; set the SEMGREP_* env vars under $TMPDIR
make test         # bare
make test-int     # bare
cd frontend && npx vitest run src/components/project/DeployStatus.test.tsx src/components/dashboard
```

## WIP `frontend/src/components/project/DeployStatus.tsx` (not committed as code)

```tsx
// Coolify deploy status (P2-14, FR-12.2). `DeployStatus` is the project card's list: per
// linked application its last deployment (status in words, time in the workspace
// timezone, short commit) and the preview links of open pull requests; a status from a
// failed poll says it is out of date. `CoolifyApps` is the Connections rail's list: the
// same, plus linking and unlinking applications by UUID (the project's `coolify_app`
// links, one versioned PATCH). The statuses come from `GET /v1/coolify/status`, which the
// worker keeps fresh; Tumnis never deploys (agents do, through Coolify's MCP server).
import { useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import type { ProjectLinkIn, ProjectOut } from "../../api/types.gen";
import { apiWrite, problemMessage, useWrite } from "../../lib/fetch";
import { formatInstant } from "../dashboard/format";

export interface DeployPreview {
  readonly pull_request_id: number;
  readonly url: string;
  readonly status: string;
  readonly commit?: string | null | undefined;
  readonly finished_at?: string | null | undefined;
}

export interface LastDeploy {
  readonly status: string;
  readonly commit?: string | null | undefined;
  readonly created_at: string;
  readonly finished_at?: string | null | undefined;
}

/** A structural subset of the generated `AppDeployStatus`. */
export interface AppDeployStatus {
  readonly app_uuid: string;
  readonly name?: string | null | undefined;
  readonly last?: LastDeploy | null | undefined;
  readonly previews: readonly DeployPreview[];
  readonly checked_at?: string | null | undefined;
  readonly error?: string | null | undefined;
}

const SHORT_SHA = 7;

function shortCommit(commit: string | null | undefined): string | null {
  if (!commit || !/^[0-9a-f]{7,}$/i.test(commit)) return null; // "HEAD" says nothing
  return commit.slice(0, SHORT_SHA);
}

/** "Deployed Mar 9, 5:03 AM", "Deploy failed …", "Deploying since …", "No deploys yet". */
export function deployWords(
  last: LastDeploy | null | undefined,
  timeZone: string,
): string {
  if (!last) return "No deploys yet";
  const at = (iso: string | null | undefined) =>
    formatInstant(iso ?? last.created_at, timeZone);
  switch (last.status) {
    case "finished":
      return `Deployed ${at(last.finished_at)}`;
    case "failed":
      return `Deploy failed ${at(last.finished_at)}`;
    case "in_progress":
      return `Deploying since ${at(last.created_at)}`;
    case "queued":
      return `Deploy queued ${at(last.created_at)}`;
    case "cancelled-by-user":
      return `Deploy cancelled ${at(last.finished_at)}`;
    default:
      return `Deploy ${last.status.replaceAll("_", " ")} ${at(last.created_at)}`;
  }
}

const TONE: Record<string, string> = {
  finished: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300",
  failed: "border-red-600/40 text-red-700 dark:text-red-300",
  in_progress: "border-amber-600/40 text-amber-700 dark:text-amber-300",
  queued: "border-amber-600/40 text-amber-700 dark:text-amber-300",
};

function AppStatus({
  app,
  timeZone,
}: {
  app: AppDeployStatus;
  timeZone: string;
}) {
  const sha = shortCommit(app.last?.commit);
  const tone = TONE[app.last?.status ?? ""] ?? "border-border text-muted";
  return (
    <li
      aria-label={app.name ?? app.app_uuid}
      className="flex min-w-0 flex-col gap-0.5"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
        <span className="min-w-0 truncate font-medium">
          {app.name ?? app.app_uuid}
        </span>
        <span
          data-testid="deploy-chip"
          data-status={app.last?.status ?? "none"}
          className={`rounded border px-1.5 ${tone}`}
        >
          {deployWords(app.last, timeZone)}
        </span>
        {sha ? <code className="text-muted">{sha}</code> : null}
        {app.error ? (
          <span className="text-muted" title="The last check failed">
            Status out of date
          </span>
        ) : null}
      </div>
      {app.previews.length > 0 ? (
        <ul aria-label="Previews" className="flex flex-wrap gap-x-3 gap-y-0.5">
          {app.previews.map((preview) => (
            <li key={preview.pull_request_id}>
              <a
                href={preview.url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-accent underline"
              >
                PR #{preview.pull_request_id} preview
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function DeployStatus({
  apps,
  timeZone,
}: {
  apps: readonly AppDeployStatus[];
  timeZone: string;
}) {
  if (apps.length === 0) return null;
  return (
    <ul aria-label="Deployments" className="flex flex-col gap-1 text-xs">
      {apps.map((app) => (
        <AppStatus key={app.app_uuid} app={app} timeZone={timeZone} />
      ))}
    </ul>
  );
}

// --- The Connections rail: link and unlink applications by UUID ---------------------------

/** Coolify's application UUIDs: lowercase letters and digits (cuid2-style). */
const APP_UUID = /^[a-z0-9]{6,64}$/;

type LinkPatch = { links: ProjectLinkIn[]; idempotencyKey?: string };

export function CoolifyApps({
  project,
  apps,
  timeZone,
}: {
  project: Pick<ProjectOut, "id" | "version" | "links">;
  apps: readonly AppDeployStatus[];
  timeZone: string;
}) {
  const queryClient = useQueryClient();
  const inputId = useId();
  const [uuid, setUuid] = useState("");
  const [invalid, setInvalid] = useState(false);
  const links = project.links ?? [];
  const linked = links.filter((l) => l.kind === "coolify_app").map((l) => l.value);
  const save = useWrite<LinkPatch, ProjectOut>({
    mutationFn: ({ links: next, idempotencyKey }) =>
      apiWrite<ProjectOut>({
        kind: "update",
        method: "PATCH",
        path: `/projects/${project.id}`,
        body: { links: next },
        version: project.version,
        idempotencyKey,
      }),
    onSuccess: () => {
      setUuid("");
      void queryClient.invalidateQueries();
    },
  });
  const withApps = (next: readonly string[]): ProjectLinkIn[] => [
    ...links.filter((l) => l.kind !== "coolify_app"),
    ...next.map((value) => ({ kind: "coolify_app" as const, value })),
  ];
  const statusOf = (value: string): AppDeployStatus =>
    apps.find((a) => a.app_uuid === value) ?? { app_uuid: value, previews: [] };

  return (
    <div className="flex flex-col gap-2 text-sm">
      {linked.length === 0 ? (
        <p className="text-muted">No Coolify apps linked.</p>
      ) : (
        <ul aria-label="Coolify apps" className="flex flex-col gap-2">
          {linked.map((value) => (
            <li key={value} className="flex items-start justify-between gap-2">
              <DeployStatus apps={[statusOf(value)]} timeZone={timeZone} />
              <button
                type="button"
                disabled={save.isPending}
                onClick={() => {
                  save.mutate({ links: withApps(linked.filter((v) => v !== value)) });
                }}
                className="shrink-0 text-xs text-muted underline"
                aria-label={`Unlink ${statusOf(value).name ?? value}`}
              >
                Unlink
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const value = uuid.trim();
          if (!APP_UUID.test(value)) {
            setInvalid(true);
            return;
          }
          setInvalid(false);
          if (!linked.includes(value)) {
            save.mutate({ links: withApps([...linked, value]) });
          }
        }}
      >
        <label htmlFor={inputId} className="flex min-w-0 flex-1 flex-col gap-1">
          Coolify app UUID
          <input
            id={inputId}
            value={uuid}
            onChange={(e) => {
              setUuid(e.target.value);
            }}
            aria-invalid={invalid}
            autoComplete="off"
            spellCheck={false}
            className="rounded border border-border bg-surface px-2 py-1"
          />
        </label>
        <button
          type="submit"
          disabled={save.isPending}
          className="rounded bg-accent px-3 py-1 text-white"
        >
          Link app
        </button>
      </form>
      {invalid ? (
        <p role="alert" className="text-xs text-red-700">
          A Coolify app UUID is letters and digits, as shown in the app&apos;s URL.
        </p>
      ) : null}
      {save.error ? (
        <p role="alert" className="text-xs text-red-700">
          {problemMessage(save.error)}
        </p>
      ) : null}
    </div>
  );
}
```
