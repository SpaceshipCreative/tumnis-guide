// The shell's destinations, in order (P0-22). Projects arrive with the dashboard (P0-23).
export interface NavItem {
  readonly to: "/" | "/tasks" | "/review" | "/search" | "/settings/$section";
  readonly label: string;
  readonly params?: { section: string };
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Dashboard" },
  { to: "/tasks", label: "Tasks" },
  { to: "/review", label: "Review" },
  { to: "/search", label: "Search" },
  {
    to: "/settings/$section",
    label: "Settings",
    params: { section: "account" },
  },
];
