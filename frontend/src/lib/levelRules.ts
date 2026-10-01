// The focus levels as the focus bar and the dashboard read them (P2-15, P4-01; FR-10.1,
// FR-10.6): each level's name, and Guardrail's one-task rules, the client's mirror of
// `focus/rules.py` (`current_guardrail_task`, `next_guardrail_task`, `guardrail_tasks`)
// over the day's plan as `GET /v1/plan/{day}` gives it.
import type { FocusCurrentOut, PlanItemViewOut } from "../api/types.gen";

export type Level = FocusCurrentOut["level"];

export const LEVEL_TEXT: Record<Level, string> = {
  quiet: "Quiet",
  nudge: "Nudge",
  coach: "Coach",
  guardrail: "Guardrail",
};

const SKIPPED = new Set(["done", "waiting_on_human"]);

/** The plan's tasks still to do, by position: accepted, live, not Done, not waiting. */
export function guardrailItems(
  items: readonly PlanItemViewOut[],
): PlanItemViewOut[] {
  return items
    .filter(
      (i) =>
        i.removed_at === null &&
        i.accepted_at !== null &&
        !SKIPPED.has(i.status),
    )
    .sort((a, b) => a.position - b.position);
}

/** The one task Guardrail shows: the In progress one, else the first still to do. */
export function currentGuardrailItem(
  items: readonly PlanItemViewOut[],
): PlanItemViewOut | undefined {
  const left = guardrailItems(items);
  return left.find((i) => i.status === "in_progress") ?? left[0];
}

/** The task after `current` by the same rule: the one prepared ahead. */
export function nextGuardrailItem(
  items: readonly PlanItemViewOut[],
  current: PlanItemViewOut | undefined,
): PlanItemViewOut | undefined {
  if (current === undefined) return undefined;
  return guardrailItems(items).find((i) => i.position > current.position);
}
