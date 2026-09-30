// The shell's destinations, in order (P0-22; the project list, P0-17), with the icon the
// collapsed sidebar shows and the group the sidebar and the phone drawer list them under
// (DS-01).
import type { IconName } from "./icons";

export interface NavItem {
  readonly to:
    "/" | "/projects" | "/tasks" | "/review" | "/search" | "/settings/$section";
  readonly label: string;
  readonly icon: IconName;
  readonly params?: { section: string };
}

export const NAV_ITEMS: readonly NavItem[] = [
  { to: "/", label: "Dashboard", icon: "dashboard" },
  { to: "/projects", label: "Projects", icon: "projects" },
  { to: "/tasks", label: "Tasks", icon: "tasks" },
  { to: "/review", label: "Review", icon: "review" },
  { to: "/search", label: "Search", icon: "search" },
  {
    to: "/settings/$section",
    label: "Settings",
    icon: "settings",
    params: { section: "account" },
  },
];

export interface NavGroup {
  readonly label: string;
  readonly items: readonly NavItem[];
}

/** The same destinations in two groups: the daily work, then finding and setting up. */
export const NAV_GROUPS: readonly NavGroup[] = [
  { label: "Work", items: NAV_ITEMS.slice(0, 4) },
  { label: "More", items: NAV_ITEMS.slice(4) },
];
