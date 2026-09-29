import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  RouterProvider,
  type AnyRouter,
} from "@tanstack/react-router";
import {
  render,
  type RenderOptions,
  type RenderResult,
} from "@testing-library/react";
import { userEvent, type UserEvent } from "@testing-library/user-event";
import { act, type ReactElement, type ReactNode } from "react";

import { createAppRouter } from "../router";

export interface RenderWithProvidersOptions extends Omit<
  RenderOptions,
  "wrapper"
> {
  /** Initial URL. P0-22 hands it to the router; until then it sets window.location. */
  route?: string;
  /** Pass one to inspect the cache; defaults to a fresh test client. */
  queryClient?: QueryClient;
}

export interface RenderWithProvidersResult extends RenderResult {
  queryClient: QueryClient;
  user: UserEvent;
}

/** A QueryClient for one test: no retries, so failures surface at once. */
export function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
}

export function renderWithProviders(
  ui: ReactElement,
  {
    route = "/",
    queryClient = createTestQueryClient(),
    ...options
  }: RenderWithProvidersOptions = {},
): RenderWithProvidersResult {
  window.history.pushState({}, "", route);
  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  }
  const user = userEvent.setup();
  return {
    ...render(ui, { ...options, wrapper: Providers }),
    queryClient,
    user,
  };
}

export interface RenderRouteResult extends RenderResult {
  router: AnyRouter;
  queryClient: QueryClient;
  user: UserEvent;
}

/**
 * The whole app at `url`: the real route tree on a memory history, loaded before the
 * first render (redirects in `beforeLoad` have run by the time it resolves).
 */
export async function renderRoute(
  url: string,
  { queryClient = createTestQueryClient() }: { queryClient?: QueryClient } = {},
): Promise<RenderRouteResult> {
  const router = createAppRouter({
    queryClient,
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  await act(() => router.load());
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...result, router, queryClient, user };
}
