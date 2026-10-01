// The plan's writes (P1-11, J1, J6): accept, remove and swap an item, split or move an
// issue, Re-plan. Each is a POST through `apiWrite` (an idempotency key per action) that
// answers the plan as it now is. Item actions show at once (optimistic) and a failure puts
// the plan back as it was (a 409 also says the plan changed elsewhere).
import { useQueryClient } from "@tanstack/react-query";

import { planningGetPlanQueryKey } from "../../api/@tanstack/react-query.gen";
import type { PlanOut } from "../../api/types.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { uiStore } from "../../stores/uiStore";
import { invalidateTaskReads } from "./queries";

export function planKey(day: string) {
  return planningGetPlanQueryKey({ path: { day } });
}

interface PlanAction {
  /** The path under /v1, e.g. `/plan/2026-03-09/items/<task>/accept`. */
  path: string;
  body?: Record<string, unknown>;
  /** The plan as it will look, shown until the server answers. */
  optimistic?: (plan: PlanOut) => PlanOut;
  idempotencyKey?: string;
}

const NOW = () => new Date().toISOString();

/** The item's row as accepted, now. */
export function accepted(taskId: string) {
  return (plan: PlanOut): PlanOut => ({
    ...plan,
    items: plan.items.map((item) =>
      item.task_id === taskId && item.removed_at === null
        ? { ...item, accepted_at: item.accepted_at ?? NOW() }
        : item,
    ),
  });
}

/** The item's row gone from the panel (kept, marked removed). */
export function removed(taskId: string) {
  return (plan: PlanOut): PlanOut => ({
    ...plan,
    items: plan.items.map((item) =>
      item.task_id === taskId && item.removed_at === null
        ? { ...item, removed_at: NOW() }
        : item,
    ),
  });
}

/** One write on the day's plan. */
export function usePlanAction(day: string) {
  const queryClient = useQueryClient();
  const key = planKey(day);
  return useWrite<PlanAction, PlanOut, { previous: PlanOut | undefined }>({
    mutationFn: ({ path, body, idempotencyKey }) =>
      apiWrite<PlanOut>({
        kind: "create",
        method: "POST",
        path,
        idempotencyKey,
        ...(body === undefined ? {} : { body }),
      }),
    onMutate: async ({ optimistic }) => {
      await queryClient.cancelQueries({ queryKey: key });
      const previous = queryClient.getQueryData<PlanOut>(key);
      if (previous && optimistic) {
        queryClient.setQueryData(key, optimistic(previous));
      }
      return { previous };
    },
    onError: (error, _vars, context) => {
      if (context?.previous) queryClient.setQueryData(key, context.previous);
      if (error instanceof ConflictError) {
        uiStore.trigger.showConflict({ entity: "plan" });
      } else {
        uiStore.trigger.showNotice({ text: "That did not save. Try again." });
      }
    },
    onSuccess: (plan) => {
      queryClient.setQueryData(key, plan);
    },
    onSettled: () => invalidateTaskReads(queryClient),
  });
}

/** `POST /v1/plan/replan` for the day: 202, the new plan arrives over the live socket. */
export function useReplan(day: string) {
  return useWrite<{ idempotencyKey?: string }, unknown>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/plan/replan",
        body: { day },
        idempotencyKey,
      }),
    onError: () => {
      uiStore.trigger.showNotice({ text: "Re-plan did not start. Try again." });
    },
  });
}
