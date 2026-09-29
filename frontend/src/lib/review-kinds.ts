// The review kind registry (P0-18's `GET /v1/review/kinds`). Seam until the route is in
// the generated client (`tasksListReviewKindsOptions`): the kinds as a list of names, or
// null when the registry cannot be read (then no kind is dropped).
import { queryOptions } from "@tanstack/react-query";

import { apiUrl } from "./fetch";

function kindsOf(body: unknown): string[] | null {
  const rows: unknown = Array.isArray(body)
    ? body
    : (body as { items?: unknown } | null)?.items;
  if (!Array.isArray(rows)) return null;
  return rows.flatMap((row: unknown) => {
    if (typeof row === "string") return [row];
    const kind = (row as { kind?: unknown } | null)?.kind;
    return typeof kind === "string" ? [kind] : [];
  });
}

export function reviewKindsOptions() {
  return queryOptions({
    queryKey: ["review-kinds"],
    queryFn: async (): Promise<string[] | null> => {
      try {
        const response = await fetch(apiUrl("/review/kinds"), {
          credentials: "same-origin",
        });
        return response.ok ? kindsOf(await response.json()) : null;
      } catch {
        return null;
      }
    },
    staleTime: 60 * 60_000,
    retry: false,
  });
}
