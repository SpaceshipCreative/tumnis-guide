/* eslint-disable @typescript-eslint/no-unused-vars -- a P0-24 spec stub */
// The plain list's order (P0-24, FR-3.7): due date ascending, undated last, then priority
// (urgent first), then title.
import type { TaskLite } from "./grouping";

export function sortByDue(_a: TaskLite, _b: TaskLite): number {
  throw new Error("not implemented: P0-24");
}
