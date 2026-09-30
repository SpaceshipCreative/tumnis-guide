// Tasks across projects (P0-22 route, P0-24 screen, FR-3.7): one plain list sorted by due
// date (undated last), then priority, then title, each task with its project. `status`
// narrows it to one status (the dashboard's "+N more" opens `?status=today`, P0-23);
// `project` to one project.
import { queryOptions, useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import * as z from "zod";

import { tasksListTasksQueryKey } from "../api/@tanstack/react-query.gen";
import { tasksListTasks } from "../api/sdk.gen";
import { zTaskOut } from "../api/zod.gen";
import { formatDay } from "../components/dashboard/format";
import { projectsQuery } from "../components/dashboard/queries";
import { STATUS_WORDS } from "../components/project/mutations";
import { TASK_PAGE_LIMIT } from "../components/project/queries";
import { sortByDue } from "../components/project/sorting";
import type { Task } from "../components/project/types";

const STATUSES = [
  "backlog",
  "today",
  "in_progress",
  "waiting_on_human",
  "in_review",
  "done",
] as const;

export const tasksSearch = z.object({
  status: z.enum(STATUSES).optional().catch(undefined),
  project: z.uuid().optional().catch(undefined),
});

// The list reads `items` and follows `next_cursor`; it never needs `total`.
const zListPage = z.object({
  items: z.array(zTaskOut),
  next_cursor: z.string().nullable(),
});

/**
 * Every page of `GET /v1/tasks` for the filter. The key is the generated op's (so LIVE_MAP
 * refreshes it) marked `pages: "all"`: its data is a plain array, never a `TaskPage`.
 */
export function allTasksQuery(filter: z.infer<typeof tasksSearch>) {
  const query = {
    limit: TASK_PAGE_LIMIT,
    ...(filter.status ? { status: filter.status } : {}),
    ...(filter.project ? { project_id: filter.project } : {}),
  };
  return queryOptions({
    queryKey: [
      { ...tasksListTasksQueryKey({ query })[0], pages: "all" },
    ] as const,
    queryFn: async ({ signal }) => {
      const items: Task[] = [];
      let cursor: string | null = null;
      do {
        const { data } = await tasksListTasks({
          query: { ...query, ...(cursor ? { cursor } : {}) },
          signal,
          throwOnError: true,
          responseValidator: async (body) => zListPage.parseAsync(body),
        });
        const page = zListPage.parse(data);
        items.push(...page.items);
        cursor = page.next_cursor;
      } while (cursor);
      return items;
    },
  });
}

export const Route = createFileRoute("/tasks")({
  validateSearch: tasksSearch,
  component: TasksPage,
});

function TasksPage() {
  const search = Route.useSearch();
  const tasks = useQuery(allTasksQuery(search));
  const projects = useQuery(projectsQuery());
  const names = new Map(
    (projects.data?.items ?? []).map((p) => [p.id, p.name] as const),
  );
  const sorted = [...(tasks.data ?? [])].sort(sortByDue);
  return (
    <section className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Tasks</h1>
      {search.status && (
        <p className="text-sm text-muted">
          Showing {STATUS_WORDS[search.status]} only.{" "}
          <Link to="/tasks" className="text-accent">
            Show all
          </Link>
        </p>
      )}
      {tasks.isPending ? (
        <p className="text-muted">Loading tasks…</p>
      ) : tasks.isError ? (
        <p role="alert" className="text-danger">
          The tasks could not be loaded.
        </p>
      ) : sorted.length === 0 ? (
        <p className="text-muted">No tasks here.</p>
      ) : (
        <ul aria-label="Tasks" className="flex flex-col gap-2">
          {sorted.map((task) => (
            <li
              key={task.id}
              data-task-title={task.title}
              className="flex flex-col gap-1 rounded-lg border border-border bg-surface px-3 py-2 md:flex-row md:items-center md:justify-between"
            >
              <Link
                to="/projects/$projectId"
                params={{ projectId: task.project_id }}
                search={{ task: task.id }}
                className="min-w-0 truncate font-medium hover:underline"
              >
                {task.title}
              </Link>
              <span className="flex flex-wrap gap-x-2 text-xs text-muted">
                <span className="rounded bg-surface-muted px-1.5 py-0.5 text-text">
                  {names.get(task.project_id) ?? "Project"}
                </span>
                <span>{STATUS_WORDS[task.status]}</span>
                <span>
                  {task.due_on
                    ? `Due ${formatDay(task.due_on)}`
                    : "No due date"}
                </span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
