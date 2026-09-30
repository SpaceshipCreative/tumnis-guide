// The Today panel (P0-23, FR-1.2): at most five tasks in the server's today order, then
// "+N more" to the full list on /tasks. A card (DS-01): the "+N more" link sits in its
// header row, and on a laptop the list scrolls inside the card, never the page.
import { Link } from "@tanstack/react-router";

import { Card } from "../common/Card";
import { TodayItem } from "./TodayItem";
import type { TodayTask } from "./types";

export const TODAY_LIMIT = 5;

export function TodayPanel({
  items,
  total,
  projectNames,
  unavailable = false,
  pending = false,
  className = "",
}: {
  items: readonly TodayTask[];
  total: number;
  projectNames: Readonly<Record<string, string>>;
  /** The Today query failed: say so instead of claiming an empty day. */
  unavailable?: boolean;
  /** No answer yet (a retry after a failure): claim nothing. */
  pending?: boolean;
  className?: string;
}) {
  const shown = items.slice(0, TODAY_LIMIT);
  const more = Math.max(0, total - shown.length);
  return (
    <Card
      title="Today"
      className={`min-h-0 ${className}`}
      bodyClassName="flex min-h-0 min-w-0 flex-1 flex-col px-4 py-2 md:overflow-y-auto"
      action={
        more > 0 && (
          <Link
            to="/tasks"
            search={{ status: "today" }}
            className="shrink-0 text-sm font-medium text-accent hover:underline"
          >
            +{more} more
          </Link>
        )
      }
    >
      {pending ? null : unavailable ? (
        <p className="text-sm text-muted">Today's tasks could not be loaded.</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted">Nothing planned for today.</p>
      ) : (
        <ol className="flex flex-col divide-y divide-border">
          {shown.map((task) => (
            <TodayItem
              key={task.id}
              task={task}
              projectName={projectNames[task.project_id] ?? "Unknown project"}
            />
          ))}
        </ol>
      )}
    </Card>
  );
}
