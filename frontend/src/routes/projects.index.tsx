// The project list (P0-17): every project, and "New project".
import { createFileRoute } from "@tanstack/react-router";

import { ProjectList } from "../components/project/ProjectList";

export const Route = createFileRoute("/projects/")({
  component: ProjectList,
});
