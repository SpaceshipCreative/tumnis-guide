// The agent activity feed (P0-23, FR-1.5; filled by P2-17): collapsed by default so it
// never crowds the screen. Opened, it reads `GET /v1/agents/feed` and shows the agents'
// task runs in four groups: running, waiting on you, finished and failed. Each run links
// to its task's run view on the project page.
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { agentsGetAgentFeedOptions } from "../../api/@tanstack/react-query.gen";
import type { AgentFeedOut, FeedRun } from "../../api/types.gen";

const GROUPS: readonly [keyof AgentFeedOut, string][] = [
  ["waiting", "Waiting on you"],
  ["running", "Running"],
  ["failed", "Failed"],
  ["finished", "Finished"],
];

function RunLine({ run }: { run: FeedRun }) {
  const title = run.task_title ?? "An agent run";
  const when = (
    <time dateTime={run.at} className="shrink-0 text-xs text-muted">
      {new Date(run.at).toLocaleString()}
    </time>
  );
  return (
    <li className="flex items-baseline justify-between gap-2 py-1 text-sm">
      {run.project_id && run.task_id ? (
        <Link
          to="/projects/$projectId"
          params={{ projectId: run.project_id }}
          search={{ task: run.task_id, run: run.run_id }}
          className="min-w-0 truncate underline-offset-2 hover:underline"
        >
          {title}
        </Link>
      ) : (
        <span className="min-w-0 truncate">{title}</span>
      )}
      {when}
    </li>
  );
}

export function ActivityFeed() {
  const [open, setOpen] = useState(false);
  const feed = useQuery({ ...agentsGetAgentFeedOptions(), enabled: open });
  const groups = GROUPS.map(
    ([key, label]) => [label, feed.data?.[key] ?? []] as const,
  ).filter(([, runs]) => runs.length > 0);
  return (
    <details
      className="shrink-0 rounded-xl border border-border bg-surface shadow-card"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
      }}
    >
      <summary className="flex min-h-11 cursor-pointer items-center px-4 text-sm font-semibold md:min-h-9">
        Agent activity
      </summary>
      <div className="border-t border-border px-4 py-3">
        {feed.isError ? (
          <p role="alert" className="text-sm text-danger">
            The agent activity could not be loaded.
          </p>
        ) : groups.length === 0 ? (
          <p className="text-sm text-muted">Nothing from the agents yet.</p>
        ) : (
          <div className="flex flex-col gap-3">
            {groups.map(([label, runs]) => (
              <section key={label} aria-label={label}>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">
                  {label}
                </h3>
                <ul className="flex flex-col">
                  {runs.map((run) => (
                    <RunLine key={run.run_id} run={run} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
    </details>
  );
}
