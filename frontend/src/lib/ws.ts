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

export function connectLive(
  qc: QueryClient,
  url: string = liveUrl(),
): () => void {
  let ws: WebSocket | undefined;
  let attempt = 0;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

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
        });
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
    ws?.close();
  };
}
