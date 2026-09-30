// The dashboard (P0-22 route, P0-23 screen). The loader prefetches every read the page
// makes, so the first render has data and no spinner (UX 1). A read that fails leaves its
// panel saying so instead of blanking the page.
import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { DashboardPage } from "../components/dashboard/DashboardPage";
import {
  deployStatusQuery,
  projectsQuery,
  reviewCountQuery,
  todayQuery,
  workspaceQuery,
} from "../components/dashboard/queries";

export const dashboardSearch = z.object({
  focus: z
    .enum(["quiet", "nudge", "coach", "guardrail"])
    .optional()
    .catch(undefined),
});

export const Route = createFileRoute("/")({
  validateSearch: dashboardSearch,
  loader: async ({ context: { queryClient } }) => {
    // Fresh cached data answers at once; a failed read is left for its panel to report.
    const settle = (read: Promise<unknown>) => read.catch(() => undefined);
    await Promise.all([
      settle(queryClient.query(projectsQuery())),
      settle(queryClient.query(todayQuery())),
      settle(queryClient.query(reviewCountQuery())),
      settle(queryClient.query(workspaceQuery())),
      settle(queryClient.query(deployStatusQuery())),
    ]);
  },
  // Commit the match at once while the loader runs (an empty page, not a spinner): the
  // location and the matched route never disagree, e.g. right after a redirect here.
  pendingMs: 0,
  pendingComponent: () => null,
  component: DashboardPage,
});
