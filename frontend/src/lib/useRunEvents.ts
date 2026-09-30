// A run's events by cursor (P2-04, FR-5.5): `GET /v1/runs/{id}/events?after_seq=&limit=`
// from the last `seq` held, page after page until a short one, appended in order and
// never fetched from the start again. The query's key is the generated
// `agentsListRunEvents` one, which LIVE_MAP lists under `run`: a live notice for the run
// (/ws) marks it stale and the next pages come in; while the run is active it also polls,
// so a lost notice only delays a line.
import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";

import { agentsListRunEventsQueryKey } from "../api/@tanstack/react-query.gen";
import { agentsListRunEvents } from "../api/sdk.gen";
import type { RunEventOut } from "../api/types.gen";

/** Events per page (the server allows up to 500). */
export const RUN_EVENTS_PAGE = 100;
/** How often an active run's log asks for new lines (a /ws notice asks sooner). */
export const RUN_EVENTS_POLL_MS = 2_000;
/** At most this many pages in one fetch, so a flood never blocks the view. */
const MAX_PAGES = 20;

interface Held {
  runId: string;
  after: number | undefined;
  events: RunEventOut[];
}

export function useRunEvents(runId: string, { active }: { active: boolean }) {
  const held = useRef<Held>({ runId, after: undefined, events: [] });
  if (held.current.runId !== runId) {
    held.current = { runId, after: undefined, events: [] };
  }
  return useQuery({
    queryKey: agentsListRunEventsQueryKey({ path: { run_id: runId } }),
    queryFn: async ({ signal }) => {
      const state = held.current;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const { data } = await agentsListRunEvents({
          path: { run_id: runId },
          query:
            state.after === undefined
              ? { limit: RUN_EVENTS_PAGE }
              : { limit: RUN_EVENTS_PAGE, after_seq: state.after },
          signal,
          throwOnError: true,
        });
        if (state !== held.current) break; // another run's view took over
        const fresh = data.items.filter(
          (event) => state.after === undefined || event.seq > state.after,
        );
        if (fresh.length > 0) {
          state.events = [...state.events, ...fresh];
          state.after = fresh.at(-1)?.seq ?? state.after;
        }
        if (data.items.length < RUN_EVENTS_PAGE) break;
      }
      return state.events;
    },
    refetchInterval: active ? RUN_EVENTS_POLL_MS : false,
    staleTime: 0,
  });
}
