// The optimistic-update pattern every edit follows (P0-22, REL-2), shown on a task:
// the edit shows at once; a 409 replaces it with the server's record (never the old
// local one) and says so; any other failure puts back what was there; either way the
// row is refetched once the write settles.
//
// `taskQueryKey` and `taskQueryOptions` name the generated `tasksGetTask` query of one
// task (P0-18), so LIVE_MAP's `tasksGetTask` matches it.
import type { z } from "zod";

import {
  tasksGetTaskOptions,
  tasksGetTaskQueryKey,
} from "../api/@tanstack/react-query.gen";
import { zTaskOut } from "../api/zod.gen";
import { uiStore } from "../stores/uiStore";
import { apiWrite, ConflictError, useWrite } from "./fetch";
import { invalidateTaskViews } from "./task-cache";
import { remember } from "./undo";

export type TaskOut = z.infer<typeof zTaskOut>;
export { zTaskOut };

export function taskQueryKey(id: string) {
  return tasksGetTaskQueryKey({ path: { task_id: id } });
}

export function taskQueryOptions(id: string) {
  return tasksGetTaskOptions({ path: { task_id: id } });
}

export interface UpdateTaskVars {
  id: string;
  patch: { title?: string } & Record<string, unknown>;
  version: number;
  idempotencyKey?: string;
}

interface Snapshot {
  key: ReturnType<typeof taskQueryKey>;
  previous: TaskOut | undefined;
}

export function useUpdateTask() {
  return useWrite<UpdateTaskVars, TaskOut, Snapshot>({
    mutationFn: (v) =>
      apiWrite({
        kind: "update",
        method: "PATCH",
        path: `/tasks/${v.id}`,
        body: v.patch,
        version: v.version,
        idempotencyKey: v.idempotencyKey,
        schema: zTaskOut,
      }),
    onMutate: async (v, ctx) => {
      const key = taskQueryKey(v.id);
      await ctx.client.cancelQueries({ queryKey: key });
      const previous = ctx.client.getQueryData<TaskOut>(key);
      ctx.client.setQueryData<TaskOut>(key, (old) =>
        old ? { ...old, ...v.patch } : old,
      );
      return { key, previous };
    },
    onSuccess: (task, v, snap, ctx) => {
      ctx.client.setQueryData(snap.key, task);
      const onlyTitle = Object.keys(v.patch).every((k) => k === "title");
      remember(task, onlyTitle ? "Title changed" : "Task changed"); // P0-24 undo
    },
    onError: (err, _v, snap, ctx) => {
      if (!snap) return;
      const current =
        err instanceof ConflictError ? zTaskOut.safeParse(err.current) : null;
      if (current?.success) {
        ctx.client.setQueryData(snap.key, current.data); // the server's record
        uiStore.trigger.showConflict({ entity: "task" });
      } else {
        ctx.client.setQueryData(snap.key, snap.previous);
      }
    },
    // The task, and the lists and boards that show it (P0-24).
    onSettled: (_d, _e, _v, _s, ctx) => invalidateTaskViews(ctx.client),
  });
}
