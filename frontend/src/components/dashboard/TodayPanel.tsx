// Stub (P0-23 spec): the Today panel.
import type { TodayTask } from "./types";

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- a stub until P0-23 lands
export function TodayPanel(_props: {
  items: readonly TodayTask[];
  total: number;
  projectNames: Readonly<Record<string, string>>;
}) {
  return null;
}
