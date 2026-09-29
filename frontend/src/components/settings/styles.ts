// Shared classes for the Settings screens (P0-26; Tailwind tokens from styles.css). Every
// control is at least 44 px tall (UX 11), so it is a comfortable target on the phone.
export const SECTION = "flex min-w-0 flex-col gap-4";
export const HEADING = "text-xl font-semibold";
export const LABEL = "flex flex-col gap-1 text-sm font-medium";
export const INPUT =
  "min-h-11 w-full min-w-0 rounded-md border border-border bg-surface px-3 py-2 text-base text-text";
export const BUTTON =
  "inline-flex min-h-11 items-center justify-center rounded-md bg-accent px-4 py-2 font-medium text-accent-contrast disabled:opacity-60";
export const SECONDARY =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-border bg-surface px-4 py-2 font-medium text-text disabled:opacity-60";
export const DANGER =
  "inline-flex min-h-11 items-center justify-center rounded-md border border-danger bg-surface px-4 py-2 font-medium text-danger disabled:opacity-60";
export const HINT = "text-sm text-muted";
export const ERROR = "text-sm text-danger";
export const CARD =
  "flex min-w-0 flex-col gap-2 rounded-md border border-border bg-surface p-3";
