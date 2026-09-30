// Shared classes for the Settings screens (P0-26), now drawn from the shared primitives
// (DS-01, common/ui.ts) so Settings, sign-in and setup share the app's look. Every
// control is at least 44 px tall (UX 11), so it is a comfortable target on the phone.
import {
  BUTTON_DANGER,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  ERROR_TEXT,
  FIELD,
  FIELD_LABEL,
  HINT as HINT_TEXT,
} from "../common/ui";

export const SECTION = "flex min-w-0 flex-col gap-4";
export const HEADING = "text-xl font-semibold";
export const LABEL = FIELD_LABEL;
export const INPUT = FIELD;
export const BUTTON = BUTTON_PRIMARY;
export const SECONDARY = BUTTON_SECONDARY;
export const DANGER = BUTTON_DANGER;
export const HINT = HINT_TEXT;
export const ERROR = ERROR_TEXT;
export const CARD =
  "flex min-w-0 flex-col gap-2 rounded-xl border border-border bg-surface p-4 shadow-card";
