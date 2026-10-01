// A run's events by cursor (P2-04, FR-5.5): `GET /v1/runs/{id}/events?after_seq=&limit=`
// from the last `seq` held, page after page until a short one, appended in order and
// never fetched from the start again. The query's key is the generated
// `agentsListRunEvents` one, which LIVE_MAP lists under `run`: a live notice for the run
// (/ws) marks it stale and the next pages come in; while the run is active it also polls,
// so a lost notice only delays a line. One fetch reads at most MAX_PAGES pages; when it
// stops at that cap on a full page, polling goes on (an ended run too) until a short
// page arrives; a failed fetch does not keep an ended run polling.
import { useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { agentsListRunEventsQueryKey } from "../api/@tanstack/react-query.gen";
import { agentsListRunEvents } from "../api/sdk.gen";
import type { RunEventOut } from "../api/types.gen";

/** Events per page (the server allows up to 500). */
export const RUN_EVENTS_PAGE = 100;
/** How often an active run's log asks for new lines (a /ws notice asks sooner). */
export const RUN_EVENTS_POLL_MS = 2_000;
/** At most this many pages in one fetch, so a flood never blocks the view. */
const MAX_PAGES = 20;

export function useRunEvents(runId: string, { active }: { active: boolean }) {
  const queryClient = useQueryClient();
  const queryKey = agentsListRunEventsQueryKey({ path: { run_id: runId } });
  const more = useRef(false); // the last fetch stopped at MAX_PAGES, not at a short page
  return useQuery({
    queryKey,
    // The events held so far live in the query's own cache entry (the cursor is the last
    // one's seq), so every view of the run, and a remount, pages on from the same place.
    queryFn: async ({ signal }) => {
      let events = queryClient.getQueryData<RunEventOut[]>(queryKey) ?? [];
      let after = events.at(-1)?.seq;
      more.current = false; // a failed fetch never keeps an ended run polling
      let full = false;
      for (let page = 0; page < MAX_PAGES; page += 1) {
        const { data } = await agentsListRunEvents({
          path: { run_id: runId },
          query:
            after === undefined
              ? { limit: RUN_EVENTS_PAGE }
              : { limit: RUN_EVENTS_PAGE, after_seq: after },
          signal,
          throwOnError: true,
        });
        const since = after;
        const fresh = data.items.filter(
          (event) => since === undefined || event.seq > since,
        );
        if (fresh.length > 0) {
          events = [...events, ...fresh];
          after = fresh.at(-1)?.seq ?? after;
        }
        full = data.items.length >= RUN_EVENTS_PAGE;
        if (!full) break;
      }
      more.current = full; // stopped at MAX_PAGES on a full page: more remain
      return events;
    },
    refetchInterval: () =>
      active || more.current ? RUN_EVENTS_POLL_MS : false,
    staleTime: 0,
  });
}
