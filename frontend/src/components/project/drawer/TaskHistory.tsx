// The drawer's History (A1.1, FR-4.2, UX 9, coordinator decision 83): the task's writes
// from its undo log, newest first, each naming the fields it changed and who made it,
// "you" for the person's own, so an override shows as theirs after a reload. The list
// refreshes with the task over /ws (LIVE_MAP); older writes come a page at a time.
import { useInfiniteQuery } from "@tanstack/react-query";
import { useId } from "react";

import { tasksListTaskHistoryInfiniteOptions } from "../../../api/@tanstack/react-query.gen";
import type { TaskChangeOut } from "../../../api/types.gen";
import { clockTime } from "../../../lib/time";
import { BUTTON_SECONDARY } from "../../common/ui";

const FIELD_WORDS: Readonly<Record<string, string>> = {
  created: "Created",
  trashed: "Moved to trash",
  restored: "Restored",
  status: "Status",
  column_id: "Column",
  board_rank: "Position",
  title: "Title",
  label: "Label",
  priority: "Priority",
  due_on: "Due date",
  estimate_minutes: "Estimate",
  first_action: "First action",
  acceptance_criteria: "Acceptance criteria",
  parent_id: "Parent task",
};

/** Who made a write, in words: the actor ref's kind (`system` or `<kind>:<uuid>`). */
export function actorWords(change: TaskChangeOut): string {
  if (change.by_you) return "you";
  const kind = change.actor.split(":", 1)[0];
  switch (kind) {
    case "system":
      return "Tumnis";
    case "task_token":
      return "the agent";
    case "api_key":
      return "an API key";
    case "device":
      return "a device";
    default:
      return "someone else";
  }
}

function changeWords(change: TaskChangeOut): string {
  const fields = change.fields.map((f) => FIELD_WORDS[f] ?? f);
  return fields.length > 0 ? fields.join(", ") : "Changed";
}

export function TaskHistory({
  taskId,
  timeZone,
}: {
  taskId: string;
  timeZone: string;
}) {
  const history = useInfiniteQuery({
    ...tasksListTaskHistoryInfiniteOptions({ path: { task_id: taskId } }),
    // The first page: the key's own params (a null page would not be read).
    initialPageParam: { path: { task_id: taskId } },
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
  const headingId = useId();
  const items = history.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-1 text-sm"
    >
      <h3 id={headingId} className="text-muted">
        History
      </h3>
      {history.isError ? (
        <p className="text-muted">The history could not be loaded.</p>
      ) : items.length === 0 ? (
        <p className="text-muted">
          {history.isPending ? "Loading…" : "No changes yet."}
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {items.map((change) => (
            <li key={change.change_id} className="flex flex-wrap gap-x-1">
              <span>{changeWords(change)}</span>
              <span className="text-muted">
                by {actorWords(change)} at {clockTime(change.at, timeZone)}
                {change.undone && " (undone)"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {history.hasNextPage && (
        <button
          type="button"
          disabled={history.isFetchingNextPage}
          onClick={() => {
            void history.fetchNextPage();
          }}
          className={`${BUTTON_SECONDARY} self-start`}
        >
          {history.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </section>
  );
}
