// A project's rule-based health in plain words (P0-23, FR-1.1, UX 10). "Blocked" says what
// it waits on: a blocked project has a task waiting on the human (P0-17 rules).
import type { Health } from "../../api/types.gen";

export const healthCopy = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked: waiting on you",
} as const satisfies Record<Health, string>;

const TONE: Record<Health, string> = {
  on_track: "border-emerald-600/40 text-emerald-700 dark:text-emerald-300",
  at_risk: "border-amber-600/50 text-amber-800 dark:text-amber-300",
  blocked: "border-danger/50 text-danger",
};

export function HealthBadge({ health }: { health: Health }) {
  const words = healthCopy[health];
  return (
    <span
      role="img"
      aria-label={`Health: ${words}`}
      className={`inline-flex shrink-0 items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONE[health]}`}
    >
      {words}
    </span>
  );
}
