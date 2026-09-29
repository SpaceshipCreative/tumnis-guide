// The review kind registry (P0-18's `GET /v1/review/kinds`, the generated
// `tasksListReviewKinds`): the kinds as a list of names, or null when the registry cannot
// be read (then no kind is dropped).
import { queryOptions } from "@tanstack/react-query";

import { tasksListReviewKindsQueryKey } from "../api/@tanstack/react-query.gen";
import { tasksListReviewKinds } from "../api/sdk.gen";

export function reviewKindsOptions() {
  return queryOptions({
    queryKey: tasksListReviewKindsQueryKey(),
    queryFn: async (): Promise<string[] | null> => {
      try {
        const { data } = await tasksListReviewKinds();
        return data ? data.items.map((row) => row.kind) : null;
      } catch {
        return null;
      }
    },
    staleTime: 60 * 60_000,
    retry: false,
  });
}
