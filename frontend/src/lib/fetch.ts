// The write path (P0-22). Spec stubs: implemented in the P0-22 green steps.
import type {
  UseMutationOptions,
  UseMutationResult,
} from "@tanstack/react-query";
import type * as z from "zod";

import type { Problem } from "../api/types.gen";

export const CSRF_COOKIE = "__Host-tumnis_csrf";

interface Base<TOut> {
  method: "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  body?: Record<string, unknown>;
  idempotencyKey: string;
  schema?: z.ZodType<TOut>;
}
export type WriteRequest<TOut> =
  | (Base<TOut> & { kind: "create" })
  | (Base<TOut> & { kind: "update"; version: number });

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly problem: Problem,
  ) {
    super(problem.title);
  }
}

export class ConflictError extends ApiError {
  /** The server's current row (REL-2). */
  get current(): unknown {
    return this.problem.current;
  }
}

export function apiWrite<TOut>(req: WriteRequest<TOut>): Promise<TOut> {
  return Promise.reject(
    new Error(`apiWrite ${req.path}: not implemented (spec:P0-22)`),
  );
}

export function useWrite<
  TVars extends { idempotencyKey?: string },
  TOut,
  TSnap,
>(
  opts: UseMutationOptions<
    TOut,
    Error,
    TVars & { idempotencyKey: string },
    TSnap
  >,
): Omit<
  UseMutationResult<TOut, Error, TVars & { idempotencyKey: string }, TSnap>,
  "mutate"
> & {
  mutate: (
    v: Omit<TVars, "idempotencyKey"> & { idempotencyKey?: string },
  ) => void;
} {
  throw new Error(
    `useWrite ${String(opts.mutationKey)}: not implemented (spec:P0-22)`,
  );
}
