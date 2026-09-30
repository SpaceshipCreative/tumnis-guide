// The review queue's reads (P1-13): the open queue (optionally one kind) and the badge
// count. Both are generated ops listed under `review_item` in LIVE_MAP, so any live
// review message refreshes them; a decision refreshes them itself too.
import { queryOptions, type QueryClient } from "@tanstack/react-query";

import { tasksListReviewOptions } from "../../api/@tanstack/react-query.gen";

export { reviewCountQuery } from "../dashboard/queries";

/** The server's page limit: the queue shows up to 200 open items. */
export const QUEUE_LIMIT = 200;

/** `GET /v1/review?limit=200[&kind=…]`: the open queue in the server's order. */
export function reviewQueueQuery(kind: string | undefined) {
  return queryOptions(
    tasksListReviewOptions({
      query:
        kind === undefined
          ? { limit: QUEUE_LIMIT }
          : { limit: QUEUE_LIMIT, kind },
    }),
  );
}

const REVIEW_READS = new Set(["tasksListReview", "tasksGetReviewCount"]);

/** Marks the queue and the count stale after a decision. */
export function invalidateReviewReads(queryClient: QueryClient): Promise<void> {
  return queryClient.invalidateQueries({
    predicate: (query) => {
      const head = query.queryKey[0] as { _id?: unknown } | undefined;
      return typeof head?._id === "string" && REVIEW_READS.has(head._id);
    },
  });
}
