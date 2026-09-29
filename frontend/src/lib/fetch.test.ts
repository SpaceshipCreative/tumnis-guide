// The write path (P0-22, REL-2): every write carries an idempotency key, the CSRF token
// and, for updates, the version it read; a retry reuses the key; reads carry none.
import { QueryClient } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { createElement, type ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { expect, test, vi } from "vitest";

import { settingsGetWorkspaceSettingsOptions } from "../api/@tanstack/react-query.gen";
import { makeTask } from "../test/factories";
import { server } from "../test/msw/server";
import { createTestQueryClient } from "../test/render";
import { apiWrite, CSRF_COOKIE, useWrite } from "./fetch";
import { useUpdateTask } from "./optimistic";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface Captured {
  method: string;
  path: string;
  headers: Headers;
  body: unknown;
}

function capture(into: Captured[]) {
  return async ({ request }: { request: Request }) => {
    const text = await request.text();
    into.push({
      method: request.method,
      path: new URL(request.url).pathname,
      headers: request.headers,
      body: text ? (JSON.parse(text) as unknown) : undefined,
    });
  };
}

function withCookie(cookie: string) {
  vi.spyOn(document, "cookie", "get").mockReturnValue(cookie);
}

function wrapper(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) =>
    createElement(QueryClientProvider, { client: queryClient }, children);
}

test.fails(
  "[P0-22][REL-2] T-P0-22-05 writes carry Idempotency-Key, CSRF and version",
  async () => {
    withCookie(`theme=dark; ${CSRF_COOKIE}=csrf-token-1; other=1`);
    const seen: Captured[] = [];
    const task = makeTask({ title: "Before", version: 3 });
    server.use(
      http.patch("/v1/tasks/:id", async (info) => {
        await capture(seen)(info);
        return HttpResponse.json({ ...task, title: "x", version: 4 });
      }),
      http.get("/v1/tasks/:id", () =>
        HttpResponse.json({ ...task, title: "x", version: 4 }),
      ),
      http.post("/v1/tasks", async (info) => {
        await capture(seen)(info);
        return HttpResponse.json(makeTask({ title: "y" }), { status: 201 });
      }),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(() => useUpdateTask(), {
      wrapper: wrapper(queryClient),
    });

    result.current.mutate({ id: task.id, patch: { title: "x" }, version: 3 });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });
    await apiWrite({
      kind: "create",
      method: "POST",
      path: "/tasks",
      body: { title: "y" },
      idempotencyKey: crypto.randomUUID(),
    });

    const [update, create] = seen;
    expect(update?.method).toBe("PATCH");
    expect(update?.path).toBe(`/v1/tasks/${task.id}`);
    expect(update?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(update?.headers.get("X-CSRF-Token")).toBe("csrf-token-1");
    expect(update?.body).toEqual({ title: "x", version: 3 });

    expect(create?.method).toBe("POST");
    expect(create?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(create?.headers.get("X-CSRF-Token")).toBe("csrf-token-1");
    expect(create?.body).toEqual({ title: "y" });
    expect(create?.body).not.toHaveProperty("version");
  },
);

test.fails(
  "[P0-22][REL-2] T-P0-22-06 a retried write reuses its key",
  async () => {
    const seen: Captured[] = [];
    server.use(
      http.post("/v1/tasks", async (info) => {
        await capture(seen)(info);
        return seen.length === 1
          ? HttpResponse.json(
              { code: "unavailable", title: "Unavailable", status: 503 },
              { status: 503 },
            )
          : HttpResponse.json(makeTask({ title: "Retried" }), { status: 201 });
      }),
    );
    const queryClient = createTestQueryClient();
    const { result } = renderHook(
      () =>
        useWrite({
          mutationFn: (v: { title: string; idempotencyKey: string }) =>
            apiWrite({
              kind: "create",
              method: "POST",
              path: "/tasks",
              body: { title: v.title },
              idempotencyKey: v.idempotencyKey,
            }),
          retry: 1,
          retryDelay: 0,
        }),
      { wrapper: wrapper(queryClient) },
    );

    result.current.mutate({ title: "Retried" });
    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true);
    });

    expect(seen).toHaveLength(2);
    const [first, second] = seen;
    expect(first?.headers.get("Idempotency-Key")).toMatch(UUID);
    expect(second?.headers.get("Idempotency-Key")).toBe(
      first?.headers.get("Idempotency-Key"),
    );
  },
);

test.fails(
  "[P0-22][REL-2] T-P0-22-07 reads carry no Idempotency-Key",
  async () => {
    withCookie(`${CSRF_COOKIE}=csrf-token-1`);
    const seen: Captured[] = [];
    server.use(
      http.get("/v1/settings/workspace", async (info) => {
        await capture(seen)(info);
        return HttpResponse.json({
          timezone: "America/New_York",
          subtask_threshold_min: 30,
          version: 1,
        });
      }),
    );
    const queryClient = createTestQueryClient();

    const data = await queryClient.query(settingsGetWorkspaceSettingsOptions());

    expect(data.timezone).toBe("America/New_York");
    expect(seen).toHaveLength(1);
    expect(seen[0]?.method).toBe("GET");
    expect(seen[0]?.headers.has("Idempotency-Key")).toBe(false);
    expect(seen[0]?.headers.has("X-CSRF-Token")).toBe(false);
  },
);
