// The Today panel (P0-23, FR-1.2): at most five tasks in the server's today order, then
// "+N more" to the full list on /tasks.
import { Link } from "@tanstack/react-router";
import { useId } from "react";

import { TodayItem } from "./TodayItem";
import type { TodayTask } from "./types";

export const TODAY_LIMIT = 5;

export function TodayPanel({
  items,
  total,
  projectNames,
  unavailable = false,
  className = "",
}: {
  items: readonly TodayTask[];
  total: number;
  projectNames: Readonly<Record<string, string>>;
  /** The Today query failed: say so instead of claiming an empty day. */
  unavailable?: boolean;
  className?: string;
}) {
  const headingId = useId();
  const shown = items.slice(0, TODAY_LIMIT);
  const more = Math.max(0, total - shown.length);
  return (
    <section
      aria-labelledby={headingId}
      className={`flex min-h-0 flex-col gap-2 ${className}`}
    >
      <h2 id={headingId} className="text-lg font-semibold">
        Today
      </h2>
      {unavailable ? (
        <p className="text-sm text-muted">Today's tasks could not be loaded.</p>
      ) : shown.length === 0 ? (
        <p className="text-sm text-muted">Nothing planned for today.</p>
      ) : (
        <ol className="flex flex-col gap-2">
          {shown.map((task) => (
            <TodayItem
              key={task.id}
              task={task}
              projectName={projectNames[task.project_id] ?? "Unknown project"}
            />
          ))}
        </ol>
      )}
      {more > 0 && (
        <Link
          to="/tasks"
          search={{ status: "today" }}
          className="self-start text-sm font-medium text-accent hover:underline"
        >
          +{more} more
        </Link>
      )}
    </section>
  );
}
