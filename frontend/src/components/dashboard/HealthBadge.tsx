// A project's rule-based health in plain words (P0-23, FR-1.1, UX 10). "Blocked" says what
// it waits on: a blocked project has a task waiting on the human (P0-17 rules).
import type { Health } from "../../api/types.gen";
import { badge, type BadgeTone } from "../common/ui";

export const healthCopy = {
  on_track: "On track",
  at_risk: "At risk",
  blocked: "Blocked: waiting on you",
} as const satisfies Record<Health, string>;

const TONE: Record<Health, BadgeTone> = {
  on_track: "success",
  at_risk: "warning",
  blocked: "danger",
};

export function HealthBadge({ health }: { health: Health }) {
  const words = healthCopy[health];
  return (
    <span
      role="img"
      aria-label={`Health: ${words}`}
      className={badge(TONE[health])}
    >
      {words}
    </span>
  );
}
