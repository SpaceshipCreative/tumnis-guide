// The reads and writes of Settings > Connections (P3-02, FR-14.4). Reads use the
// generated client's options, so a prefetch in the route's loader fills the same query;
// writes go through `apiWrite` (CSRF, one idempotency key per logical write, the version
// read on an edit). This is the only file that names the generated connection functions.
import type { QueryClient } from "@tanstack/react-query";

import {
  connectionsListConnectionsOptions,
  connectionsListConnectionsQueryKey,
  connectionsListProvidersOptions,
} from "../../../api/@tanstack/react-query.gen";
import { connectionsOauthUrl } from "../../../api/sdk.gen";
import type {
  ConnectionOut,
  ConnectionSettings,
  ConnectionsOAuthStart,
} from "../../../api/types.gen";
import { ApiError, apiWrite, ConflictError } from "../../../lib/fetch";

export const connectionsQuery = () => connectionsListConnectionsOptions();
export const connectionsQueryKey = () => connectionsListConnectionsQueryKey();
export const providersQuery = () => connectionsListProvidersOptions();

const path = (id: string, rest = "") =>
  `/connections/${encodeURIComponent(id)}${rest}`;

/** Puts a connection the server answered into the list (replacing, or adding it). */
export function storeConnection(
  queryClient: QueryClient,
  next: ConnectionOut,
): void {
  queryClient.setQueryData<ConnectionOut[]>(
    connectionsQueryKey(),
    (current) => {
      if (!current) return [next];
      return current.some((c) => c.id === next.id)
        ? current.map((c) => (c.id === next.id ? next : c))
        : [...current, next];
    },
  );
}

export function dropConnection(queryClient: QueryClient, id: string): void {
  queryClient.setQueryData<ConnectionOut[]>(connectionsQueryKey(), (current) =>
    current?.filter((c) => c.id !== id),
  );
}

export function createConnection(
  body: {
    provider: string;
    account_label: string;
    consent_acknowledged: boolean;
    settings?: ConnectionSettings;
  },
  idempotencyKey: string,
): Promise<ConnectionOut> {
  return apiWrite<ConnectionOut>({
    kind: "create",
    method: "POST",
    path: "/connections",
    body,
    idempotencyKey,
  });
}

export function updateConnection(
  connection: ConnectionOut,
  body: { account_label?: string; settings?: ConnectionSettings },
  idempotencyKey: string,
): Promise<ConnectionOut> {
  return apiWrite<ConnectionOut>({
    kind: "update",
    method: "PATCH",
    path: path(connection.id),
    body,
    version: connection.version,
    idempotencyKey,
  });
}

export function disconnectConnection(
  id: string,
  reason: string,
  idempotencyKey: string,
): Promise<undefined> {
  return apiWrite<undefined>({
    kind: "create",
    method: "DELETE",
    path: path(id),
    body: { reason },
    idempotencyKey,
  });
}

export function syncConnection(
  id: string,
  idempotencyKey: string,
): Promise<ConnectionOut> {
  return apiWrite<ConnectionOut>({
    kind: "create",
    method: "POST",
    path: path(id, "/sync"),
    idempotencyKey,
  });
}

const wait = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

export class SignInNotReady extends Error {
  constructor() {
    super("The sign-in page was not ready in time.");
    this.name = "SignInNotReady";
  }
}

/**
 * Starts the provider sign-in (`connect_oauth`) and polls until its page is ready: the
 * server never waits on the provider, so the URL may take a moment to appear.
 */
export async function signInUrl(
  id: string,
  {
    pollMs = 1000,
    attempts = 30,
  }: { pollMs?: number | undefined; attempts?: number } = {},
): Promise<string> {
  await apiWrite<ConnectionsOAuthStart>({
    kind: "create",
    method: "POST",
    path: path(id, "/oauth/start"),
    idempotencyKey: crypto.randomUUID(),
  });
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const { data } = await connectionsOauthUrl({
      path: { connection_id: id },
      throwOnError: true,
    });
    if (data.authorize_url) return data.authorize_url;
    await wait(pollMs);
  }
  throw new SignInNotReady();
}

/** What to tell the user when a read or write failed. */
export function problemText(error: unknown, fallback: string): string {
  if (error instanceof ConflictError)
    return "That changed elsewhere; the list is up to date now.";
  if (error instanceof ApiError) return error.problem.detail ?? error.message;
  if (error instanceof SignInNotReady)
    return "The sign-in page is taking too long. Try again in a moment.";
  return fallback;
}
