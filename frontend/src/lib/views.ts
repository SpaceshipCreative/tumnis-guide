// Project views (P0-22). calendar joined in P1-12; inbox and activity join in P3-07 and
// P2-10.
export const projectViews = ["tasks", "board", "calendar"] as const;
export type ProjectView = (typeof projectViews)[number];
