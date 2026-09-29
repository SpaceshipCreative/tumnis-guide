// Tasks across projects (P0-22 route; the screen arrives with P0-24).
import { createFileRoute } from "@tanstack/react-router";

import { Placeholder } from "../components/pages/Placeholder";

export const Route = createFileRoute("/tasks")({
  component: TasksPage,
});

function TasksPage() {
  return <Placeholder title="Tasks" />;
}
