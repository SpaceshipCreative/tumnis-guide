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
import { ApiError, apiWrite, useWrite } from "../../lib/fetch";
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

interface LinkPatch {
  links: ProjectLinkIn[];
  idempotencyKey?: string;
}

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
  const linked = links
    .filter((l) => l.kind === "coolify_app")
    .map((l) => l.value);
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
                  save.mutate({
                    links: withApps(linked.filter((v) => v !== value)),
                  });
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
          A Coolify app UUID is letters and digits, as shown in the app&apos;s
          URL.
        </p>
      ) : null}
      {save.error ? (
        <p role="alert" className="text-xs text-red-700">
          {save.error instanceof ApiError
            ? (save.error.problem.detail ?? save.error.message)
            : save.error.message}
        </p>
      ) : null}
    </div>
  );
}
