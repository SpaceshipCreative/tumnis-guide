// A stateful MSW fake of the reads and writes the project page makes (P0-24): the project,
// its tasks, board, columns, comments, brief and recurring tasks, and every task write
// with the change log undo reads (R-09). Writes change the fake's state, so a refetch
// after a write answers what the server would; `recorder.sent` holds every write.
//
// Shapes follow the generated types (TaskOut, BoardOut, ProjectOut, DocumentDto). The
// recurrence shapes follow P0-19's routes (`GET/PUT/DELETE /v1/tasks/{id}/recurrence`,
// `GET /v1/recurrence?project_id=`) until they are generated: a PUT takes the task's
// version for a new rule, answers `due_time` as "HH:MM:SS" and leaves the task, and the
// rule, one version on.
import { http, HttpResponse, type RequestHandler } from "msw";

import type * as z from "zod";

import type {
  AppDeployStatus,
  BoardOut,
  ColumnOut,
  CommentOut,
  PullRequestOut,
  Status,
  TaskChangeOut,
} from "../../api/types.gen";
import type { zProjectOut, zTaskOut } from "../../api/zod.gen";
import { between, nKeys } from "../../lib/rank";
import { makeProblem } from "../factories";
import { Recorder, workspaceSettings } from "./settings";

const STATUSES: readonly [string, Status][] = [
  ["Backlog", "backlog"],
  ["Today", "today"],
  ["In progress", "in_progress"],
  ["Waiting on human", "waiting_on_human"],
  ["In review", "in_review"],
  ["Done", "done"],
];

export interface RecurrenceStub {
  id: string;
  task_id: string;
  project_id?: string;
  latest_task_id?: string;
  latest_occurrence_on?: string | null;
  title: string;
  preset: "daily" | "weekdays" | "weekly" | "monthly" | null;
  cron: string | null;
  weekday: number | null;
  month_day: number | null;
  due_time: string;
  next_due_at: string | null;
  version: number;
}

export interface BriefStub {
  id: string;
  project_id: string;
  title: string;
  kind: string;
  role: string | null;
  body_md: string | null;
  trust: "trusted" | "untrusted";
  tainted: boolean;
  pinned: boolean;
  version: number;
  label: "agent" | null;
  tags: string[];
  source: string | null;
  status: "pending_scan" | "extracting" | "ready" | "quarantined" | "failed";
  status_reason: string | null;
  path: string | null;
  provider_url: string | null;
  current_version_id: string | null;
}

type ProjectOut = z.output<typeof zProjectOut>;
type TaskOut = z.output<typeof zTaskOut>;
type Row = TaskOut & { deleted?: boolean };

interface Change {
  id: string;
  taskId: string;
  before: Row;
  undone: boolean;
}

export interface ProjectFakeInit {
  project: ProjectOut;
  tasks?: readonly TaskOut[];
  recurrences?: readonly RecurrenceStub[];
  brief?: string;
  timezone?: string;
  /** `GET /v1/coolify/status`'s apps for this project (P2-14). */
  deployApps?: readonly AppDeployStatus[];
}

function without<T extends object>(value: T, key: string): Partial<T> {
  return Object.fromEntries(
    Object.entries(value).filter(([k]) => k !== key),
  ) as Partial<T>;
}

function problem(status: number, code: string, extra: object = {}) {
  return HttpResponse.json(
    { ...makeProblem({ status, code, title: code }), ...extra },
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

/** The stateful fake; `handlers` go to `server.use(...)`. */
export class ProjectFake {
  project: ProjectOut;
  readonly columns: ColumnOut[];
  readonly tasks = new Map<string, Row>();
  readonly changes = new Map<string, Change>();
  readonly comments = new Map<string, CommentOut[]>();
  /** Each task's history (A1.1), newest first: what `GET /v1/tasks/{id}/history` answers. */
  readonly history = new Map<string, TaskChangeOut[]>();
  /** Pull requests by task (P2-13): what `GET /v1/tasks/{id}/pull-requests` answers. */
  readonly pullRequests = new Map<string, PullRequestOut[]>();
  /** Answer the next link with this 422 problem code (`not_a_pull_request`, ...). */
  refuseLink: string | null = null;
  recurrences: RecurrenceStub[];
  brief: BriefStub;
  timezone: string;
  deployApps: readonly AppDeployStatus[];
  readonly recorder = new Recorder();
  /** Answer the next move with 409 `transition_not_allowed` (T-P0-24-06). */
  refuseMoves = false;
  /** While set, `POST /v1/tasks` waits for it before answering (optimistic rows). */
  createGate: Promise<void> | null = null;

  constructor(init: ProjectFakeInit) {
    this.project = init.project;
    const keys = nKeys(STATUSES.length);
    this.columns = STATUSES.map(([name, status], i) => ({
      id: crypto.randomUUID(),
      name,
      status,
      sort_key: keys[i] ?? "a0",
      version: 1,
    }));
    for (const task of init.tasks ?? []) this.put(this.place(task));
    this.recurrences = [...(init.recurrences ?? [])];
    this.brief = {
      id: crypto.randomUUID(),
      project_id: init.project.id,
      title: `${init.project.name} brief`,
      kind: "text",
      role: "brief",
      body_md: init.brief ?? "",
      trust: "trusted",
      tainted: false,
      pinned: true,
      version: 1,
      label: null,
      tags: [],
      source: "text",
      status: "ready",
      status_reason: null,
      path: null,
      provider_url: null,
      current_version_id: null,
    };
    this.timezone = init.timezone ?? "America/New_York";
    this.deployApps = init.deployApps ?? [];
  }

  /** The first column holding `status`. */
  column(status: Status): ColumnOut {
    const found = this.columns.find((c) => c.status === status);
    if (!found) throw new Error(`no column for ${status}`);
    return found;
  }

  task(id: string): Row {
    const found = this.tasks.get(id);
    if (!found) throw new Error(`no task ${id}`);
    return found;
  }

  private put(row: Row): void {
    this.tasks.set(row.id, row);
  }

  // A task in its status's column, last in it unless it already has a column.
  private place(task: TaskOut): Row {
    if (task.column_id && this.columns.some((c) => c.id === task.column_id)) {
      return { ...task };
    }
    return {
      ...task,
      column_id: this.column(task.status).id,
      board_rank: this.lastRank(this.column(task.status).id),
    };
  }

  private lastRank(columnId: string): string {
    const ranks = [...this.tasks.values()]
      .filter((t) => t.column_id === columnId && !t.deleted)
      .map((t) => t.board_rank)
      .sort();
    return between(ranks.at(-1) ?? null, null);
  }

  private live(): Row[] {
    return [...this.tasks.values()].filter((t) => !t.deleted);
  }

  private out(row: Row, changeId: string | null = null): TaskOut {
    const task = without(row, "deleted") as TaskOut;
    return { ...task, change_id: changeId };
  }

  // Applies `next` as a new version and records the change; answers the task.
  private write(before: Row, next: Row): TaskOut {
    const after: Row = {
      ...next,
      version: before.version + 1,
      updated_at: new Date().toISOString(),
    };
    this.put(after);
    const id = crypto.randomUUID();
    this.changes.set(id, { id, taskId: before.id, before, undone: false });
    return this.out(after, id);
  }

  board(): BoardOut {
    const live = this.live();
    return {
      project_id: this.project.id,
      threshold_min: this.project.subtask_threshold_min ?? 30,
      columns: this.columns.map((c) => ({
        id: c.id,
        name: c.name,
        status: c.status,
        cards: live
          .filter((t) => t.column_id === c.id && t.parent_id === null)
          .sort((a, b) => (a.board_rank < b.board_rank ? -1 : 1))
          .map((t) => ({
            task: this.out(t),
            checklist: live
              .filter((s) => s.parent_id === t.id)
              .map((s) => this.out(s)),
          })),
      })),
    };
  }

  get handlers(): RequestHandler[] {
    const pid = this.project.id;
    return [
      http.get(`/v1/projects/${pid}`, () => HttpResponse.json(this.project)),
      http.patch(`/v1/projects/${pid}`, async ({ request }) => {
        const sent = await this.recorder.record(request);
        const body = sent.body as { version: number } & Partial<ProjectOut>;
        if (body.version !== this.project.version) {
          return problem(409, "stale_version", { current: this.project });
        }
        const patch = without(body, "version");
        this.project = {
          ...this.project,
          ...patch,
          version: this.project.version + 1,
        };
        return HttpResponse.json(this.project);
      }),
      http.get("/v1/settings/workspace", () =>
        HttpResponse.json(workspaceSettings({ timezone: this.timezone })),
      ),
      http.get("/v1/coolify/status", () =>
        HttpResponse.json(
          this.deployApps.length === 0
            ? []
            : [{ project_id: pid, apps: this.deployApps }],
        ),
      ),
      http.get("/v1/tasks", ({ request }) => {
        const query = new URL(request.url).searchParams;
        const projectId = query.get("project_id");
        const status = query.get("status");
        const items = this.live()
          .filter((t) => !projectId || t.project_id === projectId)
          .filter((t) => !status || t.status === status)
          .map((t) => this.out(t));
        return HttpResponse.json({
          items,
          next_cursor: null,
          total: items.length,
        });
      }),
      http.post("/v1/tasks", async ({ request }) => {
        const sent = await this.recorder.record(request);
        const body = sent.body as { title: string; project_id: string };
        if (this.createGate) await this.createGate;
        const now = new Date().toISOString();
        const status: Status = "backlog";
        const row: Row = {
          schema_version: 1,
          id: crypto.randomUUID(),
          project_id: body.project_id,
          parent_id: null,
          title: body.title,
          label: null,
          label_source: null,
          label_reason: null,
          label_suggestion: null,
          status,
          priority: "normal",
          due_on: null,
          estimate_minutes: null,
          first_action: null,
          first_action_source: null,
          acceptance_criteria: null,
          enrichment_status: null,
          assigned_agent_id: null,
          column_id: this.column(status).id,
          board_rank: this.lastRank(this.column(status).id),
          rollover_count: 0,
          started_at: null,
          completed_at: null,
          actual_minutes: null,
          tainted: false,
          source: "user",
          version: 1,
          created_at: now,
          updated_at: now,
          change_id: null,
        };
        this.put(row);
        const id = crypto.randomUUID();
        this.changes.set(id, {
          id,
          taskId: row.id,
          before: { ...row, deleted: true },
          undone: false,
        });
        return HttpResponse.json(this.out(row, id), { status: 201 });
      }),
      http.get("/v1/tasks/:id", ({ params }) => {
        const row = this.tasks.get(String(params.id));
        if (!row || row.deleted) return problem(404, "not_found");
        return HttpResponse.json(this.out(row));
      }),
      http.patch("/v1/tasks/:id", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const row = this.tasks.get(String(params.id));
        if (!row || row.deleted) return problem(404, "not_found");
        const { version, ...patch } = sent.body as {
          version: number;
        } & Partial<TaskOut>;
        if (version !== row.version) {
          return problem(409, "stale_version", { current: this.out(row) });
        }
        return HttpResponse.json(this.write(row, { ...row, ...patch }));
      }),
      http.delete("/v1/tasks/:id", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const row = this.tasks.get(String(params.id));
        if (!row || row.deleted) return problem(404, "not_found");
        const { version } = sent.body as { version: number };
        if (version !== row.version) {
          return problem(409, "stale_version", { current: this.out(row) });
        }
        return HttpResponse.json(this.write(row, { ...row, deleted: true }));
      }),
      http.post("/v1/tasks/:id/status", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const row = this.tasks.get(String(params.id));
        if (!row || row.deleted) return problem(404, "not_found");
        const { to, version } = sent.body as { to: Status; version: number };
        if (version !== row.version) {
          return problem(409, "stale_version", { current: this.out(row) });
        }
        const column = this.column(to);
        return HttpResponse.json(
          this.write(row, {
            ...row,
            status: to,
            column_id: column.id,
            board_rank: this.lastRank(column.id),
            completed_at: to === "done" ? new Date().toISOString() : null,
          }),
        );
      }),
      http.post("/v1/tasks/:id/move", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const row = this.tasks.get(String(params.id));
        if (!row || row.deleted) return problem(404, "not_found");
        const body = sent.body as {
          column_id: string;
          board_rank: string;
          version: number;
        };
        if (body.version !== row.version) {
          return problem(409, "stale_version", { current: this.out(row) });
        }
        const column = this.columns.find((c) => c.id === body.column_id);
        if (!column) return problem(404, "not_found");
        if (this.refuseMoves) {
          return problem(409, "transition_not_allowed", {
            current: this.out(row),
          });
        }
        return HttpResponse.json(
          this.write(row, {
            ...row,
            status: column.status,
            column_id: column.id,
            board_rank: body.board_rank,
          }),
        );
      }),
      http.post("/v1/tasks/:id/undo", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const row = this.tasks.get(String(params.id));
        const body = sent.body as { change_id: string; version: number };
        const change = this.changes.get(body.change_id);
        if (change?.taskId !== row?.id || !row || !change) {
          return problem(404, "not_found");
        }
        if (change.undone) return problem(409, "already_undone");
        if (body.version !== row.version) {
          return problem(409, "stale_version", { current: this.out(row) });
        }
        change.undone = true;
        return HttpResponse.json(
          this.write(row, { ...change.before, version: row.version }),
        );
      }),
      http.get("/v1/tasks/:id/history", ({ params }) =>
        HttpResponse.json({
          items: this.history.get(String(params.id)) ?? [],
          next_cursor: null,
        }),
      ),
      http.get("/v1/tasks/:id/comments", ({ params }) =>
        HttpResponse.json({
          items: this.comments.get(String(params.id)) ?? [],
          next_cursor: null,
        }),
      ),
      http.post("/v1/tasks/:id/comments", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const taskId = String(params.id);
        const comment: CommentOut = {
          id: crypto.randomUUID(),
          task_id: taskId,
          body_md: (sent.body as { body_md: string }).body_md,
          created_by: "user:me",
          created_at: new Date().toISOString(),
          version: 1,
        };
        this.comments.set(taskId, [
          ...(this.comments.get(taskId) ?? []),
          comment,
        ]);
        return HttpResponse.json(comment, { status: 201 });
      }),
      http.get("/v1/tasks/:id/pull-requests", ({ params }) =>
        HttpResponse.json(this.pullRequests.get(String(params.id)) ?? []),
      ),
      http.post("/v1/tasks/:id/pull-requests", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        if (this.refuseLink) return problem(422, this.refuseLink);
        const taskId = String(params.id);
        const url = (sent.body as { url: string }).url;
        const match = /github\.com\/([^/]+\/[^/]+)\/pull\/(\d+)/.exec(url);
        const linked: PullRequestOut = {
          artifact_id: crypto.randomUUID(),
          url,
          repo: match?.[1] ?? "unknown/unknown",
          number: Number(match?.[2] ?? 0),
          title: null,
          state: null,
          draft: false,
          checks: null,
          review: null,
          checked_at: null,
        };
        this.pullRequests.set(taskId, [
          ...(this.pullRequests.get(taskId) ?? []),
          linked,
        ]);
        return HttpResponse.json(linked, { status: 201 });
      }),
      http.get(`/v1/projects/${pid}/board`, () =>
        HttpResponse.json(this.board()),
      ),
      http.get(`/v1/projects/${pid}/columns`, () =>
        HttpResponse.json({ project_id: pid, items: this.columns }),
      ),
      http.get(`/v1/projects/${pid}/brief`, () =>
        HttpResponse.json(this.brief),
      ),
      // The Inbox and Activity views (P2-17): empty until a test says otherwise.
      http.get(`/v1/projects/${pid}/inbox`, () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.get(`/v1/projects/${pid}/activity`, () =>
        HttpResponse.json({ items: [], next_cursor: null }),
      ),
      http.patch("/v1/knowledge/documents/:id", async ({ request }) => {
        const sent = await this.recorder.record(request);
        const body = sent.body as { body_md: string; version: number };
        if (body.version !== this.brief.version) {
          return problem(409, "stale_version", { current: this.brief });
        }
        this.brief = {
          ...this.brief,
          body_md: body.body_md,
          version: this.brief.version + 1,
        };
        return HttpResponse.json(this.brief);
      }),
      http.get("/v1/recurrence", () =>
        HttpResponse.json({
          items: this.recurrences.map((r) => ({
            project_id: this.project.id,
            latest_task_id: r.task_id,
            latest_occurrence_on: null,
            ...r,
          })),
          next_cursor: null,
        }),
      ),
      http.get("/v1/tasks/:id/recurrence", ({ params }) => {
        const found = this.recurrences.find((r) => r.task_id === params.id);
        return found ? HttpResponse.json(found) : problem(404, "not_found");
      }),
      http.put("/v1/tasks/:id/recurrence", async ({ params, request }) => {
        const sent = await this.recorder.record(request);
        const taskId = String(params.id);
        const task = this.tasks.get(taskId);
        if (!task || task.deleted) return problem(404, "not_found");
        const body = sent.body as Partial<RecurrenceStub>;
        const existing = this.recurrences.find((r) => r.task_id === taskId);
        if (body.version !== (existing?.version ?? task.version)) {
          return problem(409, "stale_version", { current: this.out(task) });
        }
        const version = task.version + 1;
        this.put({ ...task, version });
        const time = body.due_time ?? "09:00";
        const rule: RecurrenceStub = {
          id: existing?.id ?? crypto.randomUUID(),
          task_id: taskId,
          project_id: task.project_id,
          latest_task_id: taskId,
          latest_occurrence_on: task.due_on,
          title: task.title,
          preset: body.preset ?? null,
          cron: body.cron ?? null,
          weekday: body.weekday ?? null,
          month_day: body.month_day ?? null,
          due_time: time.length === 5 ? `${time}:00` : time,
          next_due_at: null,
          version,
        };
        this.recurrences = [
          ...this.recurrences.filter((r) => r.task_id !== taskId),
          rule,
        ];
        return HttpResponse.json(rule);
      }),
      http.delete("/v1/tasks/:id/recurrence", async ({ params, request }) => {
        await this.recorder.record(request);
        this.recurrences = this.recurrences.filter(
          (r) => r.task_id !== params.id,
        );
        return new HttpResponse(null, { status: 204 });
      }),
    ];
  }

  /** The writes sent to `path` (exact) with `method`. */
  sent(method: string, path: string) {
    return this.recorder.sent.filter(
      (s) => s.method === method && s.path === path,
    );
  }
}
