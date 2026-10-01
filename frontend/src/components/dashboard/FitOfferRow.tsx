// A task the plan could not place (P1-11, J6). Stub until the impl lands.
import type { PlanIssueOut } from "../../api/types.gen";

export function FitOfferRow({ issue }: { issue: PlanIssueOut; day: string }) {
  return issue.id ? null : null;
}
