// The dashboard (P0-22 route, P0-23 screen). The loader prefetches every read the page
// makes, so the first render has data and no spinner (UX 1). A read that fails leaves its
// panel saying so instead of blanking the page.
import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { focusGetCurrentOptions } from "../api/@tanstack/react-query.gen";
import { DashboardPage } from "../components/dashboard/DashboardPage";
import { localDay } from "../components/dashboard/format";
import {
  deployStatusQuery,
  justAddedQuery,
  pausesQuery,
  planQuery,
  projectsQuery,
  reviewCountQuery,
  todayQuery,
  workspaceQuery,
} from "../components/dashboard/queries";
import { loaderRead } from "../lib/loader";

export const dashboardSearch = z.object({
  focus: z
    .enum(["quiet", "nudge", "coach", "guardrail"])
    .optional()
    .catch(undefined),
  // Close the day (P1-18, J7): the panel over the dashboard, not a route of its own.
  panel: z.enum(["close"]).optional().catch(undefined),
  // The task drawer over the dashboard (A1.1, decision 83), and the run it shows (P2-04).
  task: z.uuid().optional().catch(undefined),
  run: z.uuid().optional().catch(undefined),
});

export const Route = createFileRoute("/")({
  validateSearch: dashboardSearch,
  loader: async ({ context: { queryClient } }) => {
    // Fresh cached data answers at once; a failed read is left for its panel to report,
    // and offline the loader does not wait for the network (lib/loader.ts).
    await Promise.all([
      loaderRead(queryClient, projectsQuery()),
      loaderRead(queryClient, todayQuery()),
      loaderRead(queryClient, justAddedQuery()),
      loaderRead(queryClient, reviewCountQuery()),
      // The day's plan (P1-11) is read for the workspace's own day, once its zone is in.
      loaderRead(queryClient, workspaceQuery()).then(() => {
        const workspace = queryClient.getQueryData(workspaceQuery().queryKey);
        return workspace
          ? loaderRead(
              queryClient,
              planQuery(localDay(new Date(), workspace.timezone)),
            )
          : undefined;
      }),
      loaderRead(queryClient, deployStatusQuery()),
      loaderRead(queryClient, pausesQuery()),
      // The focus level in force (P4-01): Guardrail renders the one-task view.
      loaderRead(queryClient, focusGetCurrentOptions()),
    ]);
  },
  // Commit the match at once while the loader runs (an empty page, not a spinner): the
  // location and the matched route never disagree, e.g. right after a redirect here.
  pendingMs: 0,
  pendingComponent: () => null,
  component: DashboardPage,
});
