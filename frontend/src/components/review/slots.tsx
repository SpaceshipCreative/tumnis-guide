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
  | { type: "text"; field: string; label: string }; // {field: "..."}

export interface KindSlot {
  /** A short name for the kind, shown above the title. */
  name: string;
  summary: (item: ReviewItemOut) => ReactNode;
  edit?: Editor;
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
    summary: ({ payload }) =>
      `Provisioning failed${text(payload.error) === undefined ? "" : `: ${String(payload.error)}`}. Accept retries it.`,
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
