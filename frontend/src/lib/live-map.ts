// Live entity map (P0-22). Spec stub.
export type LiveEntity =
  "task" | "project" | "review_item" | "settings" | "api_key" | "dead_letter";
export const LIVE_MAP: Record<
  LiveEntity,
  { details: string[]; lists: string[] }
> = {
  task: { details: [], lists: [] },
  project: { details: [], lists: [] },
  review_item: { details: [], lists: [] },
  settings: { details: [], lists: [] },
  api_key: { details: [], lists: [] },
  dead_letter: { details: [], lists: [] },
};
export const NOT_LIVE: readonly string[] = [];
