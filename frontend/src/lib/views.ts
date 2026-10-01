// Project views (P0-22). calendar joined in P1-12; inbox and activity in P2-17 (P3-07 fills
// the Inbox).
export const projectViews = [
  "tasks",
  "board",
  "calendar",
  "inbox",
  "activity",
] as const;
export type ProjectView = (typeof projectViews)[number];
