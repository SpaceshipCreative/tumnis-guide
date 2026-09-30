// The review queue (P1-13, FR-6.1, FR-1.4, UX 2, UX 7, UX 11, R-04): everything waiting
// for the human, in the server's order (blocking impact, then age). The heading is the
// badge count. Every decision works from the keyboard with visible hints: Enter runs the
// kind's primary action, e edits (a label: 1, 2 or 3), r rejects (or denies), s snoozes
// (then 1, 3 or t), o opens the target, j and k (or the arrows) move; after a decision
// the next item takes focus. At phone width the items stack with 44 px buttons. Each item
// is a card (DS-01, ADR-0012), in one readable column on a laptop.
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import type { ReviewItemOut } from "../../api/types.gen";
import { ApiError, apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { snoozeUntil, type SnoozeChoice } from "../../lib/time";
import { CARD } from "../common/ui";
import { workingHoursQuery, workspaceQuery } from "../settings/queries";
import {
  invalidateReviewReads,
  reviewCountQuery,
  reviewQueueQuery,
} from "./queries";
import { ResultItem } from "./ResultItem";
import {
  canOpen,
  editorFor,
  ReviewItemCard,
  SnoozeChoices,
  type DecideAction,
  type Mode,
  type SnoozeKey,
} from "./ReviewItemCard";
import { LABELS } from "./slots";

/** Marked once the queue has rendered with its data and takes input (P0-29 times it). */
export const REVIEW_READY_MARK = "tumnis:review-ready";

const SNOOZE_CHOICE: Record<SnoozeKey, SnoozeChoice> = {
  "1": "1h",
  "3": "3h",
  t: "tomorrow",
};

const HINTS: readonly { keys: string[]; text: string }[] = [
  { keys: ["Enter"], text: "primary action" },
  { keys: ["e"], text: "edit" },
  { keys: ["r"], text: "reject" },
  { keys: ["s"], text: "snooze (1, 3, t)" },
  { keys: ["o"], text: "open" },
  { keys: ["j", "k"], text: "next, previous" },
];

function deviceTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

function Shortcuts() {
  return (
    <section
      aria-label="Keyboard shortcuts"
      className="hidden flex-wrap gap-x-4 gap-y-1 text-xs text-muted md:flex"
    >
      {HINTS.map((hint) => (
        <span key={hint.text} className="inline-flex items-center gap-1">
          {hint.keys.map((key) => (
            <kbd
              key={key}
              className="rounded border border-border bg-surface px-1.5 font-mono shadow-card"
            >
              {key}
            </kbd>
          ))}
          <span>{hint.text}</span>
        </span>
      ))}
    </section>
  );
}

function isTyping(target: EventTarget): boolean {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
  );
}

interface Decision extends DecideAction {
  item: ReviewItemOut;
  idempotencyKey?: string;
}

export function ReviewQueue({
  kind,
  item: chosen,
}: {
  kind: string | undefined;
  item: string | undefined;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const queue = useQuery(reviewQueueQuery(kind));
  const count = useQuery(reviewCountQuery());
  const workspace = useQuery(workspaceQuery());
  const hours = useQuery(workingHoursQuery());
  const items = queue.data?.items ?? [];

  const [focusedId, setFocusedId] = useState<string | undefined>(undefined);
  const [mode, setMode] = useState<Mode>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const cards = useRef(new Map<string, HTMLElement>());
  const placed = useRef(false);

  const ready = queue.isSuccess;
  const marked = useRef(false);
  useLayoutEffect(() => {
    if (!ready || marked.current) return;
    marked.current = true;
    performance.mark(REVIEW_READY_MARK);
  }, [ready]);

  // The first item, or the one `item` names, takes focus once the queue has loaded.
  useEffect(() => {
    if (items.length === 0) return;
    if (focusedId !== undefined && items.some((i) => i.id === focusedId))
      return;
    const named = placed.current ? undefined : chosen;
    placed.current = true;
    setFocusedId(
      items.find((i) => i.id === named)?.id ?? items[0]?.id ?? undefined,
    );
    setMode("idle");
  }, [items, focusedId, chosen]);

  useEffect(() => {
    if (focusedId === undefined) return;
    const card = cards.current.get(focusedId);
    const active = document.activeElement;
    if (card === undefined || active === card) return;
    if (active instanceof HTMLElement && isTyping(active)) return;
    card.focus();
  }, [focusedId, items]);

  const move = useCallback(
    (step: 1 | -1) => {
      const at = items.findIndex((i) => i.id === focusedId);
      const next = items[Math.min(items.length - 1, Math.max(0, at + step))];
      if (next !== undefined) {
        setFocusedId(next.id);
        setMode("idle");
      }
    },
    [items, focusedId],
  );

  const decide = useWrite<Decision, ReviewItemOut>({
    mutationFn: ({ item, action, payload, snooze, idempotencyKey }) => {
      const until =
        snooze === undefined
          ? undefined
          : snoozeUntil(
              SNOOZE_CHOICE[snooze],
              new Date(),
              hours.data?.days ?? [],
              workspace.data?.timezone ?? deviceTimeZone(),
            ).toISOString();
      return apiWrite<ReviewItemOut>({
        kind: "update",
        method: "POST",
        path: `/review/${encodeURIComponent(item.id)}/decide`,
        body: { action, payload, snooze_until: until },
        version: item.version,
        idempotencyKey,
      });
    },
    onSuccess: (_row, { item }) => {
      setMessage(null);
      const at = items.findIndex((i) => i.id === item.id);
      const rest = items.filter((i) => i.id !== item.id);
      const next = rest[Math.min(Math.max(at, 0), rest.length - 1)];
      queryClient.setQueryData(reviewQueueQuery(kind).queryKey, (page) =>
        page === undefined ? page : { ...page, items: rest },
      );
      setFocusedId(next?.id);
      setMode("idle");
    },
    onError: (error) => {
      setMessage(
        error instanceof ConflictError
          ? "That item changed elsewhere; the queue is up to date now."
          : error instanceof ApiError
            ? (error.problem.detail ?? error.message)
            : "That did not work.",
      );
      setMode("idle");
    },
    onSettled: () => {
      void invalidateReviewReads(queryClient);
    },
  });

  const open = useCallback(
    (item: ReviewItemOut) => {
      if (item.target_type === "task" && item.project_id !== null) {
        void navigate({
          to: "/projects/$projectId",
          params: { projectId: item.project_id },
          search: { task: item.target_id },
        });
      } else if (item.target_type === "project") {
        void navigate({
          to: "/projects/$projectId",
          params: { projectId: item.target_id },
        });
      }
    },
    [navigate],
  );

  const run = (item: ReviewItemOut, decision: DecideAction) => {
    if (decide.isPending) return; // the buttons are disabled too; keys land here
    if (!item.actions.includes(decision.action)) return;
    decide.mutate({ ...decision, item });
  };

  const onKeyDown = (event: KeyboardEvent<HTMLUListElement>) => {
    const item = items.find((i) => i.id === focusedId);
    if (item === undefined || event.altKey || event.ctrlKey || event.metaKey) {
      return;
    }
    if (event.key === "Escape" && mode !== "idle") {
      setMode("idle");
      cards.current.get(item.id)?.focus();
      return;
    }
    // Keys act on the focused card itself, never on a button or field inside it.
    if (event.target !== cards.current.get(item.id) || isTyping(event.target)) {
      return;
    }
    const key = event.key;
    let handled = true;
    if (mode === "snooze") {
      if (key === "1" || key === "3" || key === "t") {
        run(item, { action: "snooze", snooze: key });
      } else handled = false;
    } else if (mode === "edit" && editorFor(item, mode)?.type === "label") {
      const label = LABELS.find((l) => l.key === key);
      if (label === undefined) handled = false;
      else run(item, { action: "edit", payload: { label: label.value } });
    } else if (key === "Enter") {
      const primary = item.primary_action ?? item.actions[0];
      if (primary === "answer" || primary === "edit") setMode(primary);
      else if (primary !== undefined) {
        run(item, { action: primary as DecideAction["action"] });
      }
    } else if (key === "e" && item.actions.includes("edit")) {
      setMode("edit");
    } else if (key === "r") {
      const refuse = item.actions.includes("reject") ? "reject" : "deny";
      run(item, { action: refuse });
    } else if (key === "s" && item.actions.includes("snooze")) {
      setMode("snooze");
    } else if (key === "o" && canOpen(item)) {
      open(item);
    } else if (key === "j" || key === "ArrowDown") {
      move(1);
    } else if (key === "k" || key === "ArrowUp") {
      move(-1);
    } else handled = false;
    if (handled) event.preventDefault();
  };

  const total = count.data?.count ?? 0;
  return (
    <section className="flex max-w-3xl flex-col gap-4">
      <header className="flex flex-col gap-2">
        <h1 className="text-2xl font-semibold">{`${String(total)} to review`}</h1>
        <Shortcuts />
      </header>
      <p role="status" className="text-sm text-muted empty:hidden">
        {message}
      </p>
      {queue.isError && (
        <p role="alert" className={`${CARD} px-4 py-3 text-sm text-danger`}>
          The review queue could not be loaded.
        </p>
      )}
      {queue.isSuccess && items.length === 0 && (
        <p className={`${CARD} px-4 py-6 text-center text-sm text-muted`}>
          Nothing waits for you.
        </p>
      )}
      <ul
        aria-label="Review queue"
        className="flex flex-col gap-3"
        onKeyDown={onKeyDown}
      >
        {items.map((item) => (
          <li key={item.id}>
            {item.kind === "result" ? (
              <ResultItem
                item={item}
                confirmReject
                current={item.id === chosen && item.id === focusedId}
                busy={decide.isPending}
                articleRef={(node) => {
                  if (node === null) cards.current.delete(item.id);
                  else cards.current.set(item.id, node);
                }}
                onFocus={() => {
                  if (item.id !== focusedId) {
                    setFocusedId(item.id);
                    setMode("idle");
                  }
                }}
                onDecide={(action, payload) => {
                  run(
                    item,
                    payload === undefined ? { action } : { action, payload },
                  );
                }}
                onSnooze={() => {
                  setFocusedId(item.id);
                  setMode("snooze");
                }}
              >
                {item.id === focusedId && mode === "snooze" && (
                  <SnoozeChoices
                    busy={decide.isPending}
                    onPick={(key) => {
                      run(item, { action: "snooze", snooze: key });
                    }}
                    onCancel={() => {
                      setMode("idle");
                    }}
                  />
                )}
              </ResultItem>
            ) : (
              <ReviewItemCard
                item={item}
                current={item.id === chosen && item.id === focusedId}
                mode={item.id === focusedId ? mode : "idle"}
                busy={decide.isPending}
                articleRef={(node) => {
                  if (node === null) cards.current.delete(item.id);
                  else cards.current.set(item.id, node);
                }}
                onFocus={() => {
                  if (item.id !== focusedId) {
                    setFocusedId(item.id);
                    setMode("idle");
                  }
                }}
                onMode={(next) => {
                  setFocusedId(item.id);
                  setMode(next);
                }}
                onDecide={(decision) => {
                  run(item, decision);
                }}
                onOpen={() => {
                  open(item);
                }}
              />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
