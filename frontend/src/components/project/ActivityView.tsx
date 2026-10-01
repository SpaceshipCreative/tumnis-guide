// The project's Activity (P2-17, FR-2.6): its agent runs, their results and its audit
// trail in one list, newest first, a page at a time ("Load more").
import { useInfiniteQuery } from "@tanstack/react-query";

import { agentsListActivityInfiniteOptions } from "../../api/@tanstack/react-query.gen";
import type { ActivityItem, RunStatus } from "../../api/types.gen";
import { Card } from "../common/Card";
import { BUTTON_SECONDARY } from "../common/ui";

const PAGE_SIZE = 50;

const RUN_STATUS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  waiting_on_human: "Waiting on you",
  held: "Held",
  succeeded: "Finished",
  failed: "Failed",
  cancelled: "Stopped",
  timed_out: "Timed out",
  runner_lost: "Lost its runner",
};

/** What happened, in a line. */
export function activityText(item: ActivityItem): string {
  const task = item.task_title ? `: ${item.task_title}` : "";
  if (item.kind === "run") {
    return `Agent run ${item.status ? RUN_STATUS[item.status].toLowerCase() : ""}${task}`;
  }
  if (item.kind === "result") {
    return `Result${task}: ${item.summary ?? ""}`;
  }
  return `${item.action ?? "Recorded"}${item.actor_type ? ` by ${item.actor_type}` : ""}`;
}

export function ActivityView({ projectId }: { projectId: string }) {
  const activity = useInfiniteQuery({
    ...agentsListActivityInfiniteOptions({
      path: { project_id: projectId },
      query: { limit: PAGE_SIZE },
    }),
    initialPageParam: null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  if (activity.isPending) {
    return <p className="text-muted">Loading the activity…</p>;
  }
  if (activity.isError) {
    return (
      <p role="alert" className="text-danger">
        The activity could not be loaded.
      </p>
    );
  }
  const items = activity.data.pages.flatMap((page) => page.items);
  return (
    <Card title="Activity" bodyClassName="px-4 py-1">
      {items.length === 0 ? (
        <p className="py-2 text-sm text-muted">
          Nothing has happened in this project yet.
        </p>
      ) : (
        <ol
          aria-label="Activity"
          className="flex flex-col divide-y divide-border"
        >
          {items.map((item) => (
            <li
              key={`${item.kind}-${item.id}`}
              className="flex flex-wrap items-baseline justify-between gap-2 py-2 text-sm"
            >
              <span className="min-w-0 break-words">{activityText(item)}</span>
              <time dateTime={item.at} className="text-xs text-muted">
                {new Date(item.at).toLocaleString()}
              </time>
            </li>
          ))}
        </ol>
      )}
      {activity.hasNextPage && (
        <button
          type="button"
          className={`${BUTTON_SECONDARY} my-2 px-3`}
          disabled={activity.isFetchingNextPage}
          onClick={() => {
            void activity.fetchNextPage();
          }}
        >
          {activity.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </Card>
  );
}
