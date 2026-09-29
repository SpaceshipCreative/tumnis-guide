// Optimistic task update (P0-22). Spec stubs.
import { queryOptions } from "@tanstack/react-query";

import type { TaskStub } from "../test/factories";

export function taskQueryOptions(id: string) {
  return queryOptions<TaskStub>({
    queryKey: [{ _id: "tasksGetTask", path: { id } }],
    queryFn: () => Promise.reject(new Error("not implemented (spec:P0-22)")),
  });
}

export interface UpdateTaskVars {
  id: string;
  patch: Partial<Pick<TaskStub, "title">>;
  version: number;
}

export function useUpdateTask(): {
  mutate: (v: UpdateTaskVars) => void;
  isSuccess: boolean;
} {
  throw new Error("useUpdateTask: not implemented (spec:P0-22)");
}
