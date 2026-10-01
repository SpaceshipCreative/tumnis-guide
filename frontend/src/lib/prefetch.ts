// Next-task preparation (P4-01, FR-10.6): fill the queries the next card reads (the task,
// and its packet, which carries the linked context items) before any click, with the
// same query keys the card and the drawer use, so the card renders from the cache.
// `queryClient.query` asks for nothing while the cached entry is fresh, and a failed read is
// swallowed: the card's own query asks again (TanStack Query v5, "Prefetching":
// https://tanstack.com/query/v5/docs/framework/react/guides/prefetching).
import type { QueryClient } from "@tanstack/react-query";

import { agentsGetTaskPacketOptions } from "../api/@tanstack/react-query.gen";
import { taskQueryOptions } from "./optimistic";

/** How long a prepared task stays fresh: the card mounting within it asks again for nothing. */
export const PREPARED_STALE_MS = 60_000;

export function taskContextQueries(taskId: string) {
  return {
    task: { ...taskQueryOptions(taskId), staleTime: PREPARED_STALE_MS },
    packet: {
      ...agentsGetTaskPacketOptions({ path: { task_id: taskId } }),
      staleTime: PREPARED_STALE_MS,
    },
  };
}

export async function prefetchTaskContext(
  queryClient: QueryClient,
  taskId: string,
): Promise<void> {
  const { task, packet } = taskContextQueries(taskId);
  const quiet = () => undefined;
  await Promise.all([
    queryClient.query(task).then(quiet, quiet),
    queryClient.query(packet).then(quiet, quiet),
  ]);
}
