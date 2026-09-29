// The one write path (P0-13 R-18, P0-22 REL-2). Reads use the generated Query options
// (src/api); every write goes through `apiWrite`, which always sends an
// `Idempotency-Key`, the session's CSRF token (read from `__Host-tumnis_csrf`) as
// `X-CSRF-Token`, and, for updates, the `version` it read: an update cannot compile
// without one. `src/lib/write-imports.test.ts` keeps generated write functions out of
// everything but this file and tests.
import {
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from "@tanstack/react-query";
import type * as z from "zod";

import type { Problem } from "../api/types.gen";

export const CSRF_COOKIE = "__Host-tumnis_csrf";
const WRITES = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/** A cookie's value from `document.cookie`, or undefined. */
export function readCookie(
  name: string,
  cookies: string = document.cookie,
): string | undefined {
  for (const part of cookies.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/** `fetch` with the write headers added; the session rides on its own cookie. */
export async function apiFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (WRITES.has(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf !== undefined && !headers.has("X-CSRF-Token")) {
      headers.set("X-CSRF-Token", csrf);
    }
    if (!headers.has("Idempotency-Key")) {
      headers.set("Idempotency-Key", crypto.randomUUID());
    }
  }
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  return fetch(path, { ...init, method, headers, credentials: "same-origin" });
}

/** The problem+json `detail` (or a fallback) of a failed response. */
export async function problemDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // not JSON
  }
  return `Request failed (${String(response.status)})`;
}

// --- 401 -----------------------------------------------------------------------------

function goToLogin(): void {
  if (window.location.pathname !== "/login") window.location.assign("/login");
}

let unauthorizedHandler: () => void = goToLogin;

/** What a 401 does: main.tsx hands in the router's navigation to /login. */
export function setUnauthorizedHandler(handler: () => void): void {
  unauthorizedHandler = handler;
}

export function onUnauthorized(): void {
  unauthorizedHandler();
}

// --- apiWrite --------------------------------------------------------------------------

/** An absolute URL for a /v1 path, on this origin. */
export function apiUrl(path: string): string {
  return new URL(`/v1${path}`, window.location.origin).href;
}

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
    this.name = "ApiError";
  }
}

/** 409: the row changed since it was read; `current` is the server's row (REL-2). */
export class ConflictError extends ApiError {
  get current(): unknown {
    return this.problem.current;
  }
}

async function readProblem(response: Response): Promise<Problem> {
  const fallback: Problem = {
    type: "about:blank",
    title: response.statusText || `Request failed (${String(response.status)})`,
    status: response.status,
    code: "unknown",
  };
  try {
    const body = (await response.json()) as Partial<Problem> | null;
    if (body && typeof body.code === "string") return { ...fallback, ...body };
  } catch {
    // not JSON
  }
  return fallback;
}

export async function apiWrite<TOut>(req: WriteRequest<TOut>): Promise<TOut> {
  const headers = new Headers({
    "Content-Type": "application/json",
    Accept: "application/json",
  });
  headers.set("Idempotency-Key", req.idempotencyKey);
  const csrf = readCookie(CSRF_COOKIE);
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const body =
    req.kind === "update" ? { ...req.body, version: req.version } : req.body;
  const response = await fetch(apiUrl(req.path), {
    method: req.method,
    headers,
    credentials: "same-origin",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 409) {
    throw new ConflictError(409, await readProblem(response));
  }
  if (response.status === 401) {
    onUnauthorized();
    throw new ApiError(401, await readProblem(response));
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readProblem(response));
  }
  const json: unknown =
    response.status === 204 ? undefined : await response.json();
  return req.schema ? req.schema.parse(json) : (json as TOut);
}

type Keyed<TVars> = TVars & { idempotencyKey: string };

/**
 * `useMutation` for writes: one idempotency key per logical write, stamped before
 * `mutate`, so a Query retry (and a replay after a lost response) reuses it.
 */
export function useWrite<
  TVars extends { idempotencyKey?: string },
  TOut,
  TSnap = unknown,
>(
  opts: UseMutationOptions<TOut, Error, Keyed<TVars>, TSnap>,
): Omit<UseMutationResult<TOut, Error, Keyed<TVars>, TSnap>, "mutate"> & {
  mutate: (
    v: Omit<TVars, "idempotencyKey"> & { idempotencyKey?: string },
  ) => void;
} {
  const mutation = useMutation(opts);
  return {
    ...mutation,
    mutate: (v) => {
      mutation.mutate({
        ...v,
        idempotencyKey: v.idempotencyKey ?? crypto.randomUUID(),
      } as Keyed<TVars>);
    },
  };
}
