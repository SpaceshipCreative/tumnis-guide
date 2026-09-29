// Test data factories. TODO(P0-11): replace these hand-written stubs with
// factories generated from the zod schemas in src/api/ (openapi-ts); the
// types below mirror TaskOut and ProjectOut from the plan until then.

export type TaskLabel = "human" | "ai" | "hybrid";
export type TaskStatus =
  | "backlog"
  | "today"
  | "in_progress"
  | "waiting_on_human"
  | "in_review"
  | "done";

export interface TaskStub {
  schema_version: 1;
  id: string;
  project_id: string;
  parent_id: string | null;
  title: string;
  label: TaskLabel | null;
  status: TaskStatus;
  version: number;
  estimate_minutes: number | null;
  first_action: string | null;
  layout: "card" | "checklist" | "nested_ai" | null;
  tainted: boolean;
}

export interface ProjectStub {
  schema_version: 1;
  id: string;
  version: number;
  name: string;
  client: string | null;
  goal: string | null;
  deadline: string | null;
  status: "active" | "on_hold" | "completed";
  brief_md: string;
  archived_at: string | null;
}

export function makeProject(overrides: Partial<ProjectStub> = {}): ProjectStub {
  return {
    schema_version: 1,
    id: crypto.randomUUID(),
    version: 1,
    name: "Test project",
    client: null,
    goal: null,
    deadline: null,
    status: "active",
    brief_md: "",
    archived_at: null,
    ...overrides,
  };
}

export function makeTask(overrides: Partial<TaskStub> = {}): TaskStub {
  return {
    schema_version: 1,
    id: crypto.randomUUID(),
    project_id: crypto.randomUUID(),
    parent_id: null,
    title: "Test task",
    label: "human",
    status: "backlog",
    version: 1,
    estimate_minutes: 30,
    first_action: null,
    layout: null,
    tainted: false,
    ...overrides,
  };
}
