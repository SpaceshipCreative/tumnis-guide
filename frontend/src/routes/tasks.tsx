// Tasks across projects (P0-22 route; the screen arrives with P0-24). `status` narrows it
// to one status: the dashboard's "+N more" opens `?status=today` (P0-23).
import { createFileRoute } from "@tanstack/react-router";
import * as z from "zod";

import { Placeholder } from "../components/pages/Placeholder";

export const tasksSearch = z.object({
  status: z
    .enum([
      "backlog",
      "today",
      "in_progress",
      "waiting_on_human",
      "in_review",
      "done",
    ])
    .optional()
    .catch(undefined),
});

export const Route = createFileRoute("/tasks")({
  validateSearch: tasksSearch,
  component: TasksPage,
});

function TasksPage() {
  return <Placeholder title="Tasks" />;
}
