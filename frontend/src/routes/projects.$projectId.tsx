// A project (P0-22 route, P0-24 page): `view` (tasks, board or calendar; absent means the
// last one used here, else tasks), `task` (the open drawer) and `week` (the Calendar
// view's ISO Monday, P1-12; absent or not a Monday means the current week) live in the
// URL. The loader prefetches the header's reads so the first render has them.
import { createFileRoute, redirect } from "@tanstack/react-router";
import * as z from "zod";

import { ProjectPage } from "../components/project/ProjectPage";
import { projectQuery, projectTasksQuery } from "../components/project/queries";
import { workspaceQuery } from "../components/settings/queries";
import { isMonday } from "../lib/time";
import { projectViews } from "../lib/views";

export { projectViews };

export const projectSearch = z.object({
  // undefined means "last used, else tasks" (P0-24)
  view: z.enum(projectViews).optional().catch(undefined),
  task: z.uuid().optional().catch(undefined),
  filter: z.enum(["open", "all"]).optional().catch(undefined),
  // An ISO Monday; anything else falls back to the current week (P0-22 rule).
  week: z.iso.date().refine(isMonday).optional().catch(undefined),
});

export const Route = createFileRoute("/projects/$projectId")({
  beforeLoad: ({ params }) => {
    if (!z.uuid().safeParse(params.projectId).success) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
      throw redirect({ to: "/" });
    }
  },
  validateSearch: projectSearch,
  loader: async ({ context: { queryClient }, params: { projectId } }) => {
    // A failed read is left for its part of the page to report.
    const settle = (read: Promise<unknown>) => read.catch(() => undefined);
    await Promise.all([
      settle(queryClient.query(projectQuery(projectId))),
      settle(queryClient.query(projectTasksQuery(projectId))),
      settle(queryClient.query(workspaceQuery())),
    ]);
  },
  pendingMs: 0,
  pendingComponent: () => null,
  component: ProjectRoute,
});

function ProjectRoute() {
  const { projectId } = Route.useParams();
  const { view, task, week } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ProjectPage
      projectId={projectId}
      view={view}
      taskId={task}
      week={week}
      onWeek={(monday) => {
        void navigate({ search: (s) => ({ ...s, week: monday }) });
      }}
      onView={(next) => {
        void navigate({ search: (s) => ({ ...s, view: next }) });
      }}
      onTask={(taskId) => {
        void navigate({ search: (s) => ({ ...s, task: taskId }) });
      }}
    />
  );
}
