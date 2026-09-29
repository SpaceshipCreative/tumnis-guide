// The shell's destinations, in order (P0-22; the project list, P0-17).
export interface NavItem {
  readonly to:
    "/" | "/projects" | "/tasks" | "/review" | "/search" | "/settings/$section";
  readonly label: string;
  readonly params?: { section: string };
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Dashboard" },
  { to: "/projects", label: "Projects" },
  { to: "/tasks", label: "Tasks" },
  { to: "/review", label: "Review" },
  { to: "/search", label: "Search" },
  {
    to: "/settings/$section",
    label: "Settings",
    params: { section: "account" },
  },
];
