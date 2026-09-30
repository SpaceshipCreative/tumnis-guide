// Shared primitives (DS-01, ADR-0012): the class sets every screen uses for buttons,
// fields, cards, badges, tables and dialogs, drawn only from the design tokens in
// styles.css so light, dark and any palette change stay in one place. The look follows
// Mosaic Lite's (rounded, bordered, a soft shadow, a violet accent), rebuilt here. Every
// control is at least 44 px tall (UX 11), so it is a comfortable target on the phone.

const CONTROL =
  "inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border px-4 py-2 text-sm font-medium shadow-card transition-colors disabled:cursor-not-allowed disabled:opacity-60";

/** The main action of a form or a dialog: the accent colour. */
export const BUTTON_PRIMARY = `${CONTROL} border-transparent bg-accent text-accent-contrast hover:bg-accent-hover`;
/** Any other action: a white (in dark, gray-800) button with a border. */
export const BUTTON_SECONDARY = `${CONTROL} border-border bg-surface text-text hover:border-border-strong`;
/** An action that deletes or signs out: danger text, danger border on hover. */
export const BUTTON_DANGER = `${CONTROL} border-border bg-surface text-danger hover:border-danger`;
/** A text-only action inside a row or a toast. */
export const BUTTON_QUIET =
  "inline-flex min-h-11 items-center justify-center rounded-lg px-2 text-sm font-medium text-accent hover:bg-accent-soft disabled:opacity-60 md:min-h-8";

/** Text inputs, selects and text areas. 16 px text on the phone so iOS does not zoom. */
export const FIELD =
  "min-h-11 w-full min-w-0 rounded-lg border border-border bg-surface px-3 py-2 text-base text-text shadow-card placeholder:text-muted hover:border-border-strong focus:border-accent md:text-sm";
/** A label above its field. */
export const FIELD_LABEL = "flex flex-col gap-1 text-sm font-medium";
export const HINT = "text-sm text-muted";
export const ERROR_TEXT = "text-sm text-danger";

/** A card: rounded, bordered, on the surface colour, with a soft shadow. */
export const CARD =
  "flex min-w-0 flex-col rounded-xl border border-border bg-surface shadow-card";
/** A card's header row: its title, and an optional action on the right. */
export const CARD_HEADER =
  "flex min-h-12 items-center justify-between gap-2 border-b border-border px-4 py-2";
export const CARD_TITLE = "min-w-0 text-base font-semibold";
export const CARD_BODY = "flex min-w-0 flex-col gap-2 p-4";

export type BadgeTone =
  "neutral" | "accent" | "success" | "warning" | "danger" | "info";

const BADGE_TONE: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-muted",
  accent: "bg-accent-soft text-accent",
  success: "bg-success-soft text-success",
  warning: "bg-warning-soft text-warning",
  danger: "bg-danger-soft text-danger",
  info: "bg-info-soft text-info",
};

/** A small rounded status label; every tone keeps 4.5:1 contrast in both themes. A
 * short label stays on one line; `wrap` lets a longer one (a status with a time) wrap
 * within its container instead of overflowing a narrow card. */
export function badge(tone: BadgeTone, { wrap = false } = {}): string {
  const fit = wrap
    ? "max-w-full rounded-xl"
    : "shrink-0 rounded-full whitespace-nowrap";
  return `inline-flex items-center px-2.5 py-0.5 text-xs font-medium ${fit} ${BADGE_TONE[tone]}`;
}

/** Tables: a muted header row, rows divided by the border colour. `relative` makes the
 * table the containing block of its `sr-only` labels, so inside a sideways-scrolling
 * wrapper they are clipped with it and never widen the page on the phone. */
export const TABLE = "relative w-full text-left text-sm";
export const TABLE_HEAD =
  "bg-surface-muted text-xs font-semibold tracking-wide text-muted uppercase";
/** Cells are tighter on the phone, and header labels may wrap there, so a table fits a
 * 375 px screen where it can instead of scrolling sideways. */
export const TABLE_HEADER_CELL = "px-2 py-2 md:px-3 md:whitespace-nowrap";
export const TABLE_BODY = "divide-y divide-border";
export const TABLE_CELL = "px-2 py-3 align-top md:px-3";

/** The dimmed page behind a modal dialog; bottom sheet on the phone, centred above. */
export const DIALOG_BACKDROP =
  "fixed inset-0 z-50 flex items-end justify-center bg-gray-900/40 p-4 sm:items-center";
/** The dialog itself. */
export const DIALOG_PANEL =
  "flex max-h-[90dvh] w-full max-w-md flex-col gap-3 overflow-y-auto rounded-xl border border-border bg-surface p-5 shadow-lg";
export const DIALOG_TITLE = "text-lg font-semibold";
