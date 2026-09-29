// The plain list's order (P0-24, FR-3.7): due date ascending, undated last, then priority
// (urgent first), then title.
import type { TaskLite } from "./grouping";

const PRIORITY_RANK: Record<TaskLite["priority"], number> = {
  urgent: 0,
  high: 1,
  normal: 2,
  low: 3,
};

export function sortByDue(a: TaskLite, b: TaskLite): number {
  if (a.due_on !== b.due_on) {
    if (a.due_on === null) return 1;
    if (b.due_on === null) return -1;
    return a.due_on < b.due_on ? -1 : 1;
  }
  const byPriority = PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
  if (byPriority !== 0) return byPriority;
  return a.title.localeCompare(b.title);
}
