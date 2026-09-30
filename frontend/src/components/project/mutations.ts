// The project page's task writes (P0-24). Each goes through `apiWrite` (one idempotency
// key per logical write), shows at once where it can, puts back what was there when the
// server says no, keeps the answered change for undo, and refreshes every task view.
import type { QueryClient } from "@tanstack/react-query";

import type { ProjectOut } from "../../api/types.gen";
import { zDocumentDto, zProjectOut, zTaskOut } from "../../api/zod.gen";
import { apiWrite, ConflictError, useWrite } from "../../lib/fetch";
import { invalidateTaskViews, queryId } from "../../lib/task-cache";
import { remember } from "../../lib/undo";
import { uiStore } from "../../stores/uiStore";
import type { MovePlan } from "../board/move";
import {
  boardQuery,
  briefQuery,
  projectQuery,
  projectTasksQuery,
} from "./queries";
import type { Board, Brief, Project, Task } from "./types";

export type Status = Task["status"];

export const STATUS_WORDS: Record<Status, string> = {
  backlog: "Backlog",
  today: "Today",
  in_progress: "In progress",
  waiting_on_human: "Waiting on you",
  in_review: "In review",
  done: "Done",
};

// Where a human can move a task from each status (P0-18's transition table), in plain
// words for a refused move (409 `transition_not_allowed`).
const REFUSED_MOVE: Record<Status, string> = {
  backlog: "Backlog tasks can move to Today or In progress",
  today: "Today tasks can move to In progress or back to Backlog",
  in_progress:
    "In progress tasks can move to Done (Human tasks only) or back to Backlog",
  waiting_on_human:
    "Tasks waiting on you can go back to In progress or to Backlog",
  in_review: "In review tasks can go back to In progress or to Done",
  done: "Done tasks can go back to Backlog",
};

/** Why a move from `from` to `to` was refused, in plain words. */
export function refusedMoveCopy(from: Status, to: Status): string {
  if (from === to) return `The task is already in ${STATUS_WORDS[to]}`;
  return REFUSED_MOVE[from];
}

const STATUS_DONE_LABEL: Record<Status, string> = {
  backlog: "Moved to Backlog",
  today: "Planned for today",
  in_progress: "Started",
  waiting_on_human: "Marked waiting on you",
  in_review: "Sent to review",
  done: "Marked done",
};

function reportFailure(error: Error): void {
  if (
    error instanceof ConflictError &&
    error.problem.code === "stale_version"
  ) {
    uiStore.trigger.showConflict({ entity: "task" });
  } else if (!(error instanceof ConflictError)) {
    uiStore.trigger.showNotice({ text: "Could not save. Try again." });
  }
}

type Page = { items: Task[] } & Record<string, unknown>;
type Snapshot = [readonly unknown[], unknown][];

/** Applies `edit` to every cached task list; answers what was there before. */
function editTaskLists(
  client: QueryClient,
  edit: (items: Task[]) => Task[],
): Snapshot {
  const lists = client.getQueriesData<Page>({
    predicate: (q) => queryId(q.queryKey) === "tasksListTasks",
  });
  for (const [key, page] of lists) {
    // `/tasks` keeps a plain array under the same op id; it refetches instead.
    if (page && Array.isArray(page.items)) {
      client.setQueryData<Page>(key, { ...page, items: edit(page.items) });
    }
  }
  return lists;
}

function restore(client: QueryClient, snapshot: Snapshot | undefined): void {
  for (const [key, data] of snapshot ?? []) client.setQueryData(key, data);
}

async function cancelTaskReads(client: QueryClient): Promise<void> {
  await client.cancelQueries({
    predicate: (q) =>
      ["tasksListTasks", "tasksGetBoard"].includes(queryId(q.queryKey) ?? ""),
  });
}

// --- Create (the composer) --------------------------------------------------------------

export interface CreateVars {
  projectId: string;
  title: string;
  idempotencyKey?: string;
}

interface CreateSnap {
  tempId: string;
  lists: Snapshot;
}

/** An optimistic row: pending label, Backlog (FR-2.5), until the server answers. */
function draftTask(id: string, projectId: string, title: string): Task {
  const now = new Date().toISOString();
  return {
    schema_version: 1,
    id,
    project_id: projectId,
    parent_id: null,
    title,
    label: null,
    label_source: null,
    status: "backlog",
    priority: "normal",
    due_on: null,
    estimate_minutes: null,
    first_action: null,
    acceptance_criteria: null,
    assigned_agent_id: null,
    column_id: null,
    board_rank: "",
    rollover_count: 0,
    started_at: null,
    completed_at: null,
    actual_minutes: null,
    tainted: false,
    source: "user",
    version: 0,
    created_at: now,
    updated_at: now,
  } as Task;
}

export function useCreateTask() {
  return useWrite<CreateVars, Task, CreateSnap>({
    mutationFn: (v) =>
      apiWrite({
        kind: "create",
        method: "POST",
        path: "/tasks",
        body: { project_id: v.projectId, title: v.title },
        idempotencyKey: v.idempotencyKey,
        schema: zTaskOut,
      }),
    onMutate: async (v, ctx) => {
      const key = projectTasksQuery(v.projectId).queryKey;
      await ctx.client.cancelQueries({ queryKey: key });
      const tempId = `draft-${crypto.randomUUID()}`;
      const previous = ctx.client.getQueryData(key);
      if (previous) {
        ctx.client.setQueryData(key, {
          ...previous,
          items: [...previous.items, draftTask(tempId, v.projectId, v.title)],
        });
      }
      return { tempId, lists: [[key, previous]] };
    },
    onSuccess: (task, v, snap, ctx) => {
      const key = projectTasksQuery(v.projectId).queryKey;
      const page = ctx.client.getQueryData(key);
      if (page) {
        ctx.client.setQueryData(key, {
          ...page,
          items: page.items.map((t) => (t.id === snap.tempId ? task : t)),
        });
      }
      remember(task, "Task added");
    },
    onError: (error, _v, snap, ctx) => {
      restore(ctx.client, snap?.lists);
      reportFailure(error);
    },
    onSettled: (_d, _e, _v, _s, ctx) => invalidateTaskViews(ctx.client),
  });
}

// --- Status -----------------------------------------------------------------------------

export interface StatusVars {
  task: Pick<Task, "id" | "version">;
  to: Status;
  idempotencyKey?: string;
}

export function useChangeStatus() {
  return useWrite<StatusVars, Task, Snapshot>({
    mutationFn: (v) =>
      apiWrite({
        kind: "update",
        method: "POST",
        path: `/tasks/${v.task.id}/status`,
        body: { to: v.to },
        version: v.task.version,
        idempotencyKey: v.idempotencyKey,
        schema: zTaskOut,
      }),
    onMutate: async (v, ctx) => {
      await cancelTaskReads(ctx.client);
      const completedAt = v.to === "done" ? new Date().toISOString() : null;
      return editTaskLists(ctx.client, (items) =>
        items.map((t) =>
          t.id === v.task.id
            ? { ...t, status: v.to, completed_at: completedAt }
            : t,
        ),
      );
    },
    onSuccess: (task, v) => {
      remember(task, STATUS_DONE_LABEL[v.to]);
    },
    onError: (error, _v, snap, ctx) => {
      restore(ctx.client, snap);
      if (
        error instanceof ConflictError &&
        error.problem.code === "transition_not_allowed"
      ) {
        uiStore.trigger.showNotice({
          text: "That status change is not allowed.",
        });
      } else {
        reportFailure(error);
      }
    },
    onSettled: (_d, _e, _v, _s, ctx) => invalidateTaskViews(ctx.client),
  });
}

// --- Move (the board) -------------------------------------------------------------------

export interface MoveVars {
  projectId: string;
  plan: MovePlan;
  idempotencyKey?: string;
}

/** The board with the card in its new column and rank (cards stay in rank order). */
export function applyMove(board: Board, plan: MovePlan): Board {
  const target = board.columns.find((c) => c.id === plan.columnId);
  let moved: Board["columns"][number]["cards"][number] | undefined;
  const columns = board.columns.map((column) => {
    const card = column.cards.find((c) => c.task.id === plan.taskId);
    if (card) moved = card;
    return card
      ? { ...column, cards: column.cards.filter((c) => c !== card) }
      : column;
  });
  if (!moved || !target) return board;
  const card = {
    ...moved,
    task: {
      ...moved.task,
      column_id: target.id,
      status: target.status,
      board_rank: plan.boardRank,
    },
  };
  return {
    ...board,
    columns: columns.map((column) =>
      column.id === target.id
        ? {
            ...column,
            cards: [...column.cards, card].sort((a, b) =>
              a.task.board_rank < b.task.board_rank ? -1 : 1,
            ),
          }
        : column,
    ),
  };
}

interface MoveSnap {
  previous: Board | undefined;
  from: Status | undefined;
  to: Status | undefined;
  toName: string;
}

export function useMoveTask() {
  return useWrite<MoveVars, Task, MoveSnap>({
    mutationFn: (v) =>
      apiWrite({
        kind: "update",
        method: "POST",
        path: `/tasks/${v.plan.taskId}/move`,
        body: { column_id: v.plan.columnId, board_rank: v.plan.boardRank },
        version: v.plan.version,
        idempotencyKey: v.idempotencyKey,
        schema: zTaskOut,
      }),
    onMutate: async (v, ctx) => {
      const key = boardQuery(v.projectId).queryKey;
      await cancelTaskReads(ctx.client);
      const previous = ctx.client.getQueryData(key);
      const from = previous?.columns.find((c) =>
        c.cards.some((card) => card.task.id === v.plan.taskId),
      )?.status;
      const target = previous?.columns.find((c) => c.id === v.plan.columnId);
      if (previous) ctx.client.setQueryData(key, applyMove(previous, v.plan));
      return { previous, from, to: target?.status, toName: target?.name ?? "" };
    },
    onSuccess: (task, _v, snap) => {
      remember(task, `Moved to ${snap.toName}`);
    },
    onError: (error, v, snap, ctx) => {
      if (snap?.previous) {
        ctx.client.setQueryData(
          boardQuery(v.projectId).queryKey,
          snap.previous,
        );
      }
      if (
        error instanceof ConflictError &&
        error.problem.code === "transition_not_allowed" &&
        snap?.from &&
        snap.to
      ) {
        uiStore.trigger.showNotice({
          text: refusedMoveCopy(snap.from, snap.to),
        });
      } else {
        reportFailure(error);
      }
    },
    onSettled: (_d, _e, _v, _s, ctx) => invalidateTaskViews(ctx.client),
  });
}

// --- Trash ------------------------------------------------------------------------------

export interface TrashVars {
  task: Pick<Task, "id" | "version">;
  idempotencyKey?: string;
}

export function useTrashTask() {
  return useWrite<TrashVars, Task, Snapshot>({
    mutationFn: (v) =>
      apiWrite({
        kind: "update",
        method: "DELETE",
        path: `/tasks/${v.task.id}`,
        body: {},
        version: v.task.version,
        idempotencyKey: v.idempotencyKey,
        schema: zTaskOut,
      }),
    onMutate: async (v, ctx) => {
      await cancelTaskReads(ctx.client);
      return editTaskLists(ctx.client, (items) =>
        items.filter((t) => t.id !== v.task.id),
      );
    },
    onSuccess: (task) => {
      remember(task, "Moved to trash");
    },
    onError: (error, _v, snap, ctx) => {
      restore(ctx.client, snap);
      reportFailure(error);
    },
    onSettled: (_d, _e, _v, _s, ctx) => invalidateTaskViews(ctx.client),
  });
}

// --- Project and brief (the Context rail) ----------------------------------------------

export interface ProjectPatchVars {
  project: Pick<Project, "id" | "version">;
  patch: Record<string, unknown>;
  idempotencyKey?: string;
}

/** `PATCH /v1/projects/{id}` with the version read; a conflict shows the server's copy. */
export function useUpdateProject() {
  return useWrite<ProjectPatchVars, ProjectOut>({
    mutationFn: async (v) => {
      const project = await apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/projects/${v.project.id}`,
        body: v.patch,
        version: v.project.version,
        idempotencyKey: v.idempotencyKey,
        schema: zProjectOut,
      });
      return project as ProjectOut;
    },
    onSuccess: (project, _v, _s, ctx) => {
      ctx.client.setQueryData(projectQuery(project.id).queryKey, project);
    },
    onError: (error, v, _s, ctx) => {
      const current =
        error instanceof ConflictError
          ? zProjectOut.safeParse(error.current)
          : null;
      if (current?.success) {
        ctx.client.setQueryData(
          projectQuery(v.project.id).queryKey,
          current.data as ProjectOut,
        );
        uiStore.trigger.showConflict({ entity: "project" });
      } else {
        reportFailure(error);
      }
    },
    onSettled: (_d, _e, v, _s, ctx) =>
      ctx.client.invalidateQueries({
        queryKey: projectQuery(v.project.id).queryKey,
      }),
  });
}

export interface BriefVars {
  projectId: string;
  brief: Pick<Brief, "id" | "version">;
  bodyMd: string;
  idempotencyKey?: string;
}

/** `PATCH /v1/knowledge/documents/{id} {body_md, version}`: the brief is a text entry. */
export function useSaveBrief() {
  return useWrite<BriefVars, Brief>({
    mutationFn: (v) =>
      apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/knowledge/documents/${v.brief.id}`,
        body: { body_md: v.bodyMd },
        version: v.brief.version,
        idempotencyKey: v.idempotencyKey,
        schema: zDocumentDto,
      }),
    onSuccess: (brief, v, _s, ctx) => {
      ctx.client.setQueryData(briefQuery(v.projectId).queryKey, brief);
    },
    onError: (error, v, _s, ctx) => {
      const current =
        error instanceof ConflictError
          ? zDocumentDto.safeParse(error.current)
          : null;
      if (current?.success) {
        ctx.client.setQueryData(briefQuery(v.projectId).queryKey, current.data);
        uiStore.trigger.showConflict({ entity: "brief" });
      } else {
        reportFailure(error);
      }
    },
  });
}
