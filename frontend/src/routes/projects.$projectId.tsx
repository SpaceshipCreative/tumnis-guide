// A project (P0-22 route; the header shows its name from P0-17; the views arrive with
// P0-24).
import { useQuery } from "@tanstack/react-query";
import { createFileRoute, redirect } from "@tanstack/react-router";
import { useSelector } from "@xstate/store-react";
import * as z from "zod";

import { projectsGetProjectOptions } from "../api/@tanstack/react-query.gen";
import { Placeholder } from "../components/pages/Placeholder";
import { projectViews } from "../lib/views";
import { uiStore } from "../stores/uiStore";

export { projectViews };

export const projectSearch = z.object({
  // undefined means "last used, else tasks" (P0-24)
  view: z.enum(projectViews).optional().catch(undefined),
  task: z.uuid().optional().catch(undefined),
  filter: z.enum(["open", "all"]).optional().catch(undefined),
});

export const Route = createFileRoute("/projects/$projectId")({
  beforeLoad: ({ params }) => {
    if (!z.uuid().safeParse(params.projectId).success) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
      throw redirect({ to: "/" });
    }
  },
  validateSearch: projectSearch,
  component: ProjectPage,
});

function ProjectPage() {
  const { projectId } = Route.useParams();
  const { view } = Route.useSearch();
  const lastView = useSelector(uiStore, (s) => s.context.lastView[projectId]);
  const project = useQuery(
    projectsGetProjectOptions({ path: { project_id: projectId } }),
  );
  return (
    <Placeholder title={project.data?.name ?? "Project"}>
      <p className="text-muted">View: {view ?? lastView ?? "tasks"}</p>
    </Placeholder>
  );
}
