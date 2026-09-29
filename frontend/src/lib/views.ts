// Project views (P0-22). calendar, inbox and activity join in P1-12, P3-07 and P2-10.
export const projectViews = ["tasks", "board"] as const;
export type ProjectView = (typeof projectViews)[number];
