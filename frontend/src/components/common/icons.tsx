// The shell's icons (DS-01): simple 20 px line drawings in the current text colour, drawn
// for this app. Decorative: the control around each one carries the accessible name.
import type { ReactNode } from "react";

export type IconName =
  | "dashboard"
  | "projects"
  | "tasks"
  | "review"
  | "search"
  | "settings"
  | "bell"
  | "help"
  | "menu"
  | "close"
  | "collapse"
  | "expand"
  | "user"
  | "check";

const PATHS: Record<IconName, ReactNode> = {
  dashboard: (
    <>
      <rect x="3" y="3" width="6" height="7" rx="1.5" />
      <rect x="11" y="3" width="6" height="4" rx="1.5" />
      <rect x="3" y="12" width="6" height="5" rx="1.5" />
      <rect x="11" y="9" width="6" height="8" rx="1.5" />
    </>
  ),
  projects: (
    <path d="M3 6.5A1.5 1.5 0 0 1 4.5 5H8l1.5 1.5h6A1.5 1.5 0 0 1 17 8v6.5a1.5 1.5 0 0 1-1.5 1.5h-11A1.5 1.5 0 0 1 3 14.5Z" />
  ),
  tasks: (
    <>
      <rect x="3" y="3" width="14" height="14" rx="3" />
      <path d="m7 10 2 2 4-4" />
    </>
  ),
  review: (
    <>
      <rect x="3" y="4" width="14" height="12" rx="2" />
      <path d="M3 11h4l1 2h4l1-2h4" />
    </>
  ),
  search: (
    <>
      <circle cx="9" cy="9" r="5" />
      <path d="m13 13 4 4" />
    </>
  ),
  settings: (
    <>
      <path d="M3 6h14M3 14h14" />
      <circle cx="7" cy="6" r="2" fill="var(--tg-surface)" />
      <circle cx="13" cy="14" r="2" fill="var(--tg-surface)" />
    </>
  ),
  bell: (
    <>
      <path d="M6 8a4 4 0 0 1 8 0c0 4 2 5 2 5H4s2-1 2-5Z" />
      <path d="M8.5 16a1.5 1.5 0 0 0 3 0" />
    </>
  ),
  help: (
    <>
      <circle cx="10" cy="10" r="7" />
      <path d="M8 8a2 2 0 1 1 3 1.7c-.6.4-1 .8-1 1.5" />
      <path d="M10 13.75v.01" />
    </>
  ),
  menu: <path d="M3 5h14M3 10h14M3 15h14" />,
  close: <path d="m5 5 10 10M15 5 5 15" />,
  collapse: <path d="m12 5-5 5 5 5" />,
  expand: <path d="m8 5 5 5-5 5" />,
  user: (
    <>
      <circle cx="10" cy="7" r="3" />
      <path d="M4 17a6 6 0 0 1 12 0" />
    </>
  ),
  check: <path d="m5 10 3 3 7-7" />,
};

export function Icon({
  name,
  className = "size-5",
}: {
  name: IconName;
  className?: string;
}) {
  return (
    <svg
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
    >
      {PATHS[name]}
    </svg>
  );
}

/** The app's mark: a rounded violet square with a T. */
export function BrandMark({ className = "size-8" }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      aria-hidden="true"
      focusable="false"
      className={`shrink-0 ${className}`}
    >
      <rect width="32" height="32" rx="8" fill="var(--tg-accent)" />
      <path
        d="M10 11h12M16 11v11"
        stroke="var(--tg-accent-contrast)"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}
