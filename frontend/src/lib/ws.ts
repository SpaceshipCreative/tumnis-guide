// The live socket (P0-22, ADR-0004): `/ws` sends `{entity, id}` for every committed
// change in this workspace, never data; each message marks stale exactly the queries
// LIVE_MAP names, and the refetch goes through the API (and RLS) as usual. After a drop
// it reconnects with jittered backoff and, once back, invalidates everything: whatever
// changed while it was away is refetched.
import type { QueryClient } from "@tanstack/react-query";
import * as z from "zod";

import { LIVE_MAP, type LiveEntity } from "./live-map";

export interface LiveMessage {
  entity: LiveEntity;
  id: string;
}

const liveMessage = z.object({
  entity: z.enum(Object.keys(LIVE_MAP) as [LiveEntity, ...LiveEntity[]]),
  id: z.string().min(1),
});

/** A frame as a LiveMessage, or null for pings and anything unknown. */
export function parseLiveMessage(data: unknown): LiveMessage | null {
  if (typeof data !== "string") return null;
  try {
    const parsed = liveMessage.safeParse(JSON.parse(data));
    return parsed.success ? parsed.data : null;
  } catch {
    return null;
  }
}

export function matchesLive(
  queryKey: readonly unknown[],
  msg: LiveMessage,
): boolean {
  const head = queryKey[0] as
    { _id?: unknown; path?: Record<string, unknown> } | null | undefined;
  if (
    typeof head !== "object" ||
    head === null ||
    typeof head._id !== "string"
  ) {
    return false;
  }
  const entry = LIVE_MAP[msg.entity];
  if (entry.lists.includes(head._id)) return true;
  return (
    entry.details.includes(head._id) &&
    Object.values(head.path ?? {}).includes(msg.id)
  );
}

export function liveUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss" : "ws";
  return `${scheme}://${window.location.host}/ws`;
}

const MAX_DELAY_MS = 30_000;
const BASE_DELAY_MS = 500;
/** The live reads' window: a message in a quiet moment is read again at once, and the
 * messages that follow within this long are read together when it ends. */
const LIVE_WINDOW_MS = 200;

export function connectLive(
  qc: QueryClient,
  url: string = liveUrl(),
): () => void {
  let ws: WebSocket | undefined;
  let attempt = 0;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  // One change often sends a run of messages within a second (one per row it touched: a
  // quick-add recomputes the blocking impact of the open review items in its project).
  // Each message used to start its own refetch, cancelling the one before, so a shown
  // query was read once per message: ten review counts in the second after one quick-add
  // in an e2e run, 22 in 5 s, against a session burst of 50 requests. Now every message
  // marks its queries stale as it comes; the first after a quiet moment is read at once
  // (a label still shows within a second of Enter, A1.1), and the rest of its window are
  // read together when the window ends. That later read restarts one still running, so
  // it cannot miss what the window's messages announced.
  let pending: LiveMessage[] = [];
  let windowTimer: ReturnType<typeof setTimeout> | undefined;
  const refetch = (messages: LiveMessage[]) => {
    void qc.refetchQueries({
      type: "active",
      predicate: (query) =>
        messages.some((msg) => matchesLive(query.queryKey, msg)),
    });
  };
  const endWindow = () => {
    windowTimer = undefined;
    if (pending.length === 0) return; // quiet: the next message reads at once
    const messages = pending;
    pending = [];
    refetch(messages);
    windowTimer = setTimeout(endWindow, LIVE_WINDOW_MS);
  };
  const read = (msg: LiveMessage) => {
    if (windowTimer === undefined) {
      refetch([msg]);
      windowTimer = setTimeout(endWindow, LIVE_WINDOW_MS);
    } else {
      pending.push(msg);
    }
  };

  const open = () => {
    ws = new WebSocket(url);
    ws.onopen = () => {
      if (attempt > 0) void qc.invalidateQueries(); // catch up after a drop
      attempt = 0;
    };
    ws.onmessage = (event: MessageEvent) => {
      const msg = parseLiveMessage(event.data);
      if (msg) {
        void qc.invalidateQueries({
          predicate: (query) => matchesLive(query.queryKey, msg),
          refetchType: "none", // read below, at most twice per window
        });
        read(msg);
      }
    };
    ws.onclose = () => {
      if (stopped) return;
      const delay =
        Math.min(MAX_DELAY_MS, BASE_DELAY_MS * 2 ** attempt) *
        (0.5 + Math.random() / 2);
      attempt += 1;
      timer = setTimeout(open, delay);
    };
  };
  open();
  return () => {
    stopped = true;
    clearTimeout(timer);
    clearTimeout(windowTimer);
    ws?.close();
  };
}
