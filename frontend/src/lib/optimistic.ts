// The optimistic-update pattern every edit follows (P0-22, REL-2), shown on a task:
// the edit shows at once; a 409 replaces it with the server's record (never the old
// local one) and says so; any other failure puts back what was there; either way the
// row is refetched once the write settles.
//
// Seam until the tasks routes are generated (P0-18): `zTaskOut`, `taskQueryKey` and
// `taskQueryOptions` stand in for the generated `zTaskOut`, `tasksGetTaskQueryKey` and
// `tasksGetTaskOptions`, with the same key shape, so LIVE_MAP's `tasksGetTask` matches.
import { queryOptions } from "@tanstack/react-query";
import * as z from "zod";

import { uiStore } from "../stores/uiStore";
import { apiUrl, apiWrite, ConflictError, useWrite } from "./fetch";

export interface TaskOut {
  id: string;
  title: string;
  version: number;
}
// Loose: the fields this pattern needs are checked, the rest of the row is kept.
export const zTaskOut: z.ZodType<TaskOut> = z.looseObject({
  id: z.string(),
  title: z.string(),
  version: z.int(),
});

export function taskQueryKey(id: string) {
  return [
    { _id: "tasksGetTask", baseUrl: window.location.origin, path: { id } },
  ] as const;
}

export function taskQueryOptions(id: string) {
  return queryOptions({
    queryKey: taskQueryKey(id),
    queryFn: async ({ signal }): Promise<TaskOut> => {
      const response = await fetch(apiUrl(`/tasks/${id}`), {
        credentials: "same-origin",
        signal,
      });
      if (!response.ok) throw new Error(`GET task ${String(response.status)}`);
      return zTaskOut.parse(await response.json());
    },
  });
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
    onSuccess: (task, _v, snap, ctx) => {
      ctx.client.setQueryData(snap.key, task);
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
    onSettled: (_d, _e, v, _s, ctx) =>
      ctx.client.invalidateQueries({ queryKey: taskQueryKey(v.id) }),
  });
}
