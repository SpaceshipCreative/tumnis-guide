// The dashboard at Guardrail (P4-01, FR-10.6, FR-10.9): one task at a time. The card shows
// the plan's current task (the In progress one, else the first still to do) with its
// project, label, estimate, first action and linked context, and Start or Done; the rest
// of Today is only a count. The next task is prepared before any click (its task and
// packet in the cache, `prefetchTaskContext`), so on Done it renders at once. The calendar
// strip stays (FR-1.3), and "Show everything for now" lowers today's level one step.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { focusGetCurrentQueryKey } from "../../api/@tanstack/react-query.gen";
import type { FocusCurrentOut, PlanItemViewOut } from "../../api/types.gen";
import { zFocusCurrentOut } from "../../api/zod.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import {
  currentGuardrailItem,
  guardrailItems,
  nextGuardrailItem,
} from "../../lib/levelRules";
import { prefetchTaskContext, taskContextQueries } from "../../lib/prefetch";
import { uiStore } from "../../stores/uiStore";
import { Card } from "../common/Card";
import { BUTTON_PRIMARY, BUTTON_QUIET, BUTTON_SECONDARY } from "../common/ui";
import { CalendarStrip } from "./CalendarStrip";
import { formatMinutes } from "./format";
import { useReplan } from "./planWrites";
import { planQuery, todayQuery } from "./queries";

const LABEL_TEXT: Record<string, string> = {
  human: "Human",
  ai: "AI",
  hybrid: "Hybrid",
};

// The untrusted-data wrapper a packet puts around outside text (P2-08); the card shows
// only the text, which React renders as text.
const UNTRUSTED_TAG = /<\/?untrusted-data\b[^>]*>/g;

/** The linked context items' text, from the task's packet. */
function contextTexts(body: Record<string, unknown> | undefined): string[] {
  const items = body?.context_items;
  if (!Array.isArray(items)) return [];
  return items
    .map((item: unknown) => {
      const rendered = (item as { rendered?: unknown } | null)?.rendered;
      return typeof rendered === "string"
        ? rendered.replace(UNTRUSTED_TAG, "").trim()
        : "";
    })
    .filter((text) => text !== "");
}

function primaryAction(
  item: PlanItemViewOut,
): { to: "in_progress" | "done"; text: string } | null {
  if (item.status === "today" || item.status === "backlog") {
    return { to: "in_progress", text: "Start" };
  }
  if (item.status === "in_progress" && item.label === "human") {
    return { to: "done", text: "Done" };
  }
  return null;
}

function GuardrailCard({ item, day }: { item: PlanItemViewOut; day: string }) {
  const queryClient = useQueryClient();
  const queries = taskContextQueries(item.task_id);
  const task = useQuery(queries.task);
  const packet = useQuery(queries.packet);
  const change = useWrite<
    { to: string; version: number; idempotencyKey?: string },
    unknown
  >({
    mutationFn: ({ to, version, idempotencyKey }) =>
      apiWrite({
        kind: "update",
        method: "POST",
        path: `/tasks/${item.task_id}/status`,
        body: { to },
        version,
        idempotencyKey,
      }),
    onError: (error) => {
      if (error instanceof ConflictError) {
        uiStore.trigger.showConflict({ entity: "task" });
      }
    },
    // The plan and Today move on; the next card's prepared reads stay as they are.
    onSettled: () =>
      Promise.all([
        queryClient.invalidateQueries({ queryKey: planQuery(day).queryKey }),
        queryClient.invalidateQueries({ queryKey: todayQuery().queryKey }),
        queryClient.invalidateQueries({ queryKey: queries.task.queryKey }),
        queryClient.invalidateQueries({ queryKey: focusGetCurrentQueryKey() }),
      ]),
  });
  const action = primaryAction(item);
  const version = task.data?.version;
  const context = contextTexts(packet.data?.body);
  const facts = [
    item.project_name,
    item.label === null ? null : (LABEL_TEXT[item.label] ?? item.label),
    item.estimate_minutes === null || item.label === "ai"
      ? null
      : formatMinutes(item.estimate_minutes),
  ].filter((f): f is string => f !== null);

  return (
    <article data-testid="today-item" className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-col gap-1">
        <h3 className="text-lg font-semibold break-words">{item.title}</h3>
        <p className="text-sm text-muted">{facts.join(" · ")}</p>
      </div>
      {item.first_action !== null && (
        <p className="text-sm break-words">{`First step: ${item.first_action}`}</p>
      )}
      {context.length > 0 && (
        <ul aria-label="Linked context" className="flex flex-col gap-1 text-sm">
          {context.map((text, i) => (
            <li
              key={i}
              className="rounded border border-border px-2 py-1 break-words"
            >
              {text}
            </li>
          ))}
        </ul>
      )}
      {action !== null && (
        <div className="flex gap-2">
          <button
            type="button"
            className={`${BUTTON_PRIMARY} min-h-11 flex-1 md:flex-none`}
            disabled={version === undefined || change.isPending}
            onClick={() => {
              if (version !== undefined)
                change.mutate({ to: action.to, version });
            }}
          >
            {action.text}
          </button>
        </div>
      )}
    </article>
  );
}

function NothingPlanned({ day }: { day: string }) {
  const replan = useReplan(day);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <p>Nothing planned. Re-plan?</p>
      <button
        type="button"
        className={BUTTON_SECONDARY}
        disabled={replan.isPending}
        onClick={() => {
          replan.mutate({});
        }}
      >
        Re-plan
      </button>
    </div>
  );
}

export function GuardrailDashboard({
  planDay,
}: {
  planDay: string | undefined;
}) {
  const queryClient = useQueryClient();
  const plan = useQuery({
    ...planQuery(planDay ?? ""),
    enabled: planDay !== undefined,
  });
  const items = plan.data?.items ?? [];
  const current = currentGuardrailItem(items);
  const next = nextGuardrailItem(items, current);
  const remaining =
    guardrailItems(items).length - (current === undefined ? 0 : 1);

  const nextId = next?.task_id;
  useEffect(() => {
    if (nextId !== undefined) void prefetchTaskContext(queryClient, nextId);
  }, [queryClient, nextId]);

  // "Show everything for now" (FR-10.9): today's level one step lower, until day close.
  const less = useWrite<{ idempotencyKey?: string }, FocusCurrentOut>({
    mutationFn: ({ idempotencyKey }) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/focus/less",
        body: {},
        idempotencyKey,
        schema: zFocusCurrentOut,
      }),
    onSuccess: (answer) => {
      queryClient.setQueryData(focusGetCurrentQueryKey(), answer);
    },
  });

  return (
    <div className="flex min-w-0 flex-col gap-4 md:mx-auto md:w-full md:max-w-2xl">
      {planDay !== undefined && <CalendarStrip day={planDay} />}
      <Card title="Now">
        {current !== undefined && planDay !== undefined ? (
          <GuardrailCard key={current.task_id} item={current} day={planDay} />
        ) : plan.isPending && planDay !== undefined ? null : plan.isError ? (
          <p className="text-sm text-muted">
            Today's plan could not be loaded.
          </p>
        ) : (
          planDay !== undefined && <NothingPlanned day={planDay} />
        )}
        {remaining > 0 && (
          <p className="text-sm text-muted">{`${String(remaining)} more today`}</p>
        )}
      </Card>
      <button
        type="button"
        className={`${BUTTON_QUIET} self-start`}
        disabled={less.isPending}
        onClick={() => {
          less.mutate({});
        }}
      >
        Show everything for now
      </button>
    </div>
  );
}
