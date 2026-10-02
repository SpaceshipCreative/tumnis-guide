// Per-kind slots of the review item renderer (P1-13): what an item says and how its
// `edit` (or `answer`) collects a payload. A kind without a slot renders with the generic
// one (its payload's plain fields), so a kind registered later (questions, approvals,
// proposals) shows with no change here; it adds a slot only to say more.
import type { ReactNode } from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import { formatMinutes } from "../dashboard/format";

/** How an item's `edit` or `answer` collects its payload. */
export type Editor =
  | { type: "label" } // 1/2/3 picks Human, AI or Hybrid: {label}
  | { type: "number"; field: string; label: string } // {field: n}
  | { type: "text"; field: string; label: string } // {field: "..."}
  | { type: "none" }; // no form: the action is decided at once, with no payload

export interface KindSlot {
  /** A short name for the kind, shown above the title. */
  name: string;
  summary: (item: ReviewItemOut) => ReactNode;
  edit?: Editor;
  /** Button words for actions whose plain name would mislead ("Accept" by default). */
  actionWords?: Partial<Record<string, string>>;
  /** A badge beside the kind's name, such as "Overnight". */
  tag?: string;
  /** The kind's actions this item has nothing to offer for, each with the reason (the
   * owning module would refuse them). They are not shown, and no key decides them. */
  unavailable?: (item: ReviewItemOut) => Partial<Record<string, string>>;
}

export const LABELS = [
  { key: "1", value: "human", text: "Human" },
  { key: "2", value: "ai", text: "AI" },
  { key: "3", value: "hybrid", text: "Hybrid" },
] as const;

function text(value: unknown): string | undefined {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : undefined;
}

function labelText(value: unknown): string {
  return LABELS.find((l) => l.value === value)?.text ?? String(value);
}

function percent(value: unknown): string | undefined {
  return typeof value === "number"
    ? `${String(Math.round(value * 100))}%`
    : undefined;
}

/** `Thu 1 Oct` for a calendar day (`YYYY-MM-DD`), as the Today panel's offers read. */
function shortDay(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return day;
  const format = (options: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-GB", { ...options, timeZone: "UTC" }).format(
      date,
    );
  return `${format({ weekday: "short" })} ${format({ day: "numeric", month: "short" })}`;
}

/** A plan issue's offered split, in minutes (none when it has no split). */
function splitOf(payload: Record<string, unknown>): number[] {
  return Array.isArray(payload.split)
    ? payload.split.filter((n): n is number => typeof n === "number")
    : [];
}

function minutes(value: unknown): string | undefined {
  return typeof value === "number" ? formatMinutes(value) : undefined;
}

const SLOTS: Record<string, KindSlot> = {
  low_confidence_label: {
    name: "Label",
    summary: ({ payload }) => {
      const suggested = payload.suggested;
      const probabilities = payload.probabilities as
        Record<string, unknown> | undefined;
      const odds = percent(probabilities?.[String(suggested)]);
      return (
        <>
          Suggested: {labelText(suggested)}
          {odds === undefined ? "" : ` (${odds})`}
          {text(payload.reason) === undefined
            ? ""
            : `. ${String(payload.reason)}`}
        </>
      );
    },
    edit: { type: "label" },
  },
  estimate_outlier: {
    name: "Estimate",
    summary: ({ payload }) => {
      const estimate = minutes(payload.estimate_minutes) ?? "No estimate";
      const flag =
        payload.flag === "too_high" ? "looks too high" : "looks too low";
      const median = minutes(payload.history_median);
      return `${estimate} ${flag}${median === undefined ? "" : `; similar work took ${median}`}`;
    },
    edit: {
      type: "number",
      field: "estimate_minutes",
      label: "Estimate in minutes",
    },
  },
  provisioning_failed: {
    name: "Agent provisioning",
    summary: ({ payload }) => {
      // P1-06's workflow may leave `error` empty and give only its `error_code`.
      const reason = text(payload.error) ?? text(payload.error_code);
      return `Provisioning failed${reason === undefined ? "" : `: ${reason}`}. Accept retries it.`;
    },
  },
  // A task queued to run unattended that the window refused (P4-04, FR-4.5, SAF-1): the
  // reason in plain words; accept takes it off the queue, snooze asks again later.
  unattended_refused: {
    name: "Did not run overnight",
    summary: ({ payload }) =>
      `${text(payload.reason) ?? "It could not run unattended"}. Take it off the queue, or snooze to keep it queued.`,
    actionWords: { accept: "Take off the queue" },
    tag: "Overnight",
  },
  plan_issue: {
    // P1-11's PlanIssuePayload: accept takes the split, edit the move, reject keeps the
    // task off the day (APP-12: a sentence, not the payload's fields). Edit takes no
    // payload (the kind defines none), and an offer the item lacks is not an action.
    name: "Plan",
    summary: ({ payload }) => {
      const day = text(payload.day);
      const reason = text(payload.reason);
      const split = splitOf(payload);
      const moveTo = text(payload.move_to);
      const offers = [
        ...(split.length > 0
          ? [`Accept splits it into ${split.join(" + ")} min`]
          : []),
        ...(moveTo === undefined
          ? []
          : [`Edit moves it to ${shortDay(moveTo)}`]),
        "Reject keeps it off that day",
      ];
      const why =
        reason === undefined
          ? ""
          : ` (${reason.charAt(0).toLowerCase()}${reason.slice(1)})`;
      return `Doesn't fit ${day === undefined ? "the day" : shortDay(day)}${why}. ${offers.join("; ")}.`;
    },
    edit: { type: "none" },
    unavailable: ({ payload }) => ({
      ...(splitOf(payload).length === 0
        ? { accept: "Accept is not offered: this task has no split." }
        : {}),
      ...(text(payload.move_to) === undefined
        ? { edit: "Edit is not offered: no later day has room for this task." }
        : {}),
    }),
  },
  decision_unavailable: {
    name: "Decision",
    summary: ({ payload }) =>
      `No provider answered ${text(payload.point) ?? "a decision"}; accept asks again.`,
    edit: { type: "text", field: "value", label: "Value" },
  },
};

/** The generic slot: the kind's name and the payload's plain fields. */
function genericSlot(kind: string): KindSlot {
  return {
    name: kind.replaceAll("_", " "),
    summary: ({ payload }) =>
      Object.entries(payload)
        .flatMap(([key, value]) => {
          const shown = text(value);
          return shown === undefined || key.endsWith("_id")
            ? []
            : [`${key.replaceAll("_", " ")}: ${shown}`];
        })
        .join("; "),
    edit: { type: "text", field: "value", label: "Value" },
  };
}

/** The answer form of every kind that offers `answer` (questions, P2-05). */
export const ANSWER: Editor = { type: "text", field: "text", label: "Answer" };

export function slotFor(kind: string): KindSlot {
  return SLOTS[kind] ?? genericSlot(kind);
}

/** Why each of the item's actions it has no offer for is not available. */
export function unavailableActions(
  item: ReviewItemOut,
): Partial<Record<string, string>> {
  return slotFor(item.kind).unavailable?.(item) ?? {};
}

/** The item's actions it can be decided with: the kind's, less any it has no offer for. */
export function offeredActions(item: ReviewItemOut): string[] {
  const unavailable = unavailableActions(item);
  return item.actions.filter((action) => unavailable[action] === undefined);
}
