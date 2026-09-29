// Stub (P0-23 spec): rule-based health in plain words.
import type { Health } from "../../api/types.gen";

export const healthCopy = {} as Record<Health, string>;

// eslint-disable-next-line @typescript-eslint/no-unused-vars -- a stub until P0-23 lands
export function HealthBadge(_props: { health: Health }) {
  return null;
}
