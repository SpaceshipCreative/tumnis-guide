import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
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
import { vi } from "vitest";

import { createAppRouter } from "../router";
import { trackRouter } from "./routers";

/** The two layouts every UI change is checked at (AGENTS.md: 375 and 1280 px). */
export type Viewport = "phone" | "laptop";

const VIEWPORT_SIZE: Record<Viewport, { width: number; height: number }> = {
  phone: { width: 375, height: 812 },
  laptop: { width: 1280, height: 800 },
};

// `(min-width: 768px)` and `(max-width: 767px)` style queries against a width.
function matchesWidth(query: string, width: number): boolean {
  const min = /min-width:\s*(\d+)px/.exec(query);
  const max = /max-width:\s*(\d+)px/.exec(query);
  if (min && width < Number(min[1])) return false;
  if (max && width > Number(max[1])) return false;
  return Boolean(min ?? max);
}

/**
 * Sizes jsdom's window like the device (jsdom has no layout): `innerWidth`,
 * `innerHeight` and a `matchMedia` answering width queries, so layout branches in
 * script render as they would there. Vitest's `unstubGlobals` puts them back.
 */
export function setViewport(viewport: Viewport): void {
  const { width, height } = VIEWPORT_SIZE[viewport];
  vi.stubGlobal("innerWidth", width);
  vi.stubGlobal("innerHeight", height);
  vi.stubGlobal("matchMedia", (query: string): MediaQueryList => {
    const list = {
      matches: matchesWidth(query, width),
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    };
    return list as MediaQueryList;
  });
}

export interface RenderWithProvidersOptions extends Omit<
  RenderOptions,
  "wrapper"
> {
  /** Initial URL. P0-22 hands it to the router; until then it sets window.location. */
  route?: string;
  /** Pass one to inspect the cache; defaults to a fresh test client. */
  queryClient?: QueryClient;
  /** Size the window like a phone or a laptop first (see `setViewport`). */
  viewport?: Viewport;
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
    viewport,
    ...options
  }: RenderWithProvidersOptions = {},
): RenderWithProvidersResult {
  if (viewport) setViewport(viewport);
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

// Renders a loaded router, tracked until it is unmounted so that setup.ts can let its
// loads finish after the test (routers.ts, issue #76).
function renderRouter(
  router: AnyRouter,
  queryClient: QueryClient,
): RenderRouteResult {
  const user = userEvent.setup();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  const untrack = trackRouter(router);
  const unmount = () => {
    untrack();
    result.unmount();
    // Nothing renders this router now. Its load still running would hand the commit to
    // the unmounted tree (and wait forever for a render); router-core's own transition,
    // the one it uses before any provider mounts, commits without React instead.
    router.startTransition = (commit) => {
      commit();
      return Promise.resolve(false);
    };
  };
  return { ...result, unmount, router, queryClient, user };
}

/**
 * The whole app at `url`: the real route tree on a memory history, loaded before the
 * first render (redirects in `beforeLoad` have run by the time it resolves).
 */
export async function renderRoute(
  url: string,
  {
    queryClient = createTestQueryClient(),
    viewport,
  }: { queryClient?: QueryClient; viewport?: Viewport } = {},
): Promise<RenderRouteResult> {
  if (viewport) setViewport(viewport);
  const router = createAppRouter({
    queryClient,
    history: createMemoryHistory({ initialEntries: [url] }),
  });
  // Routes that show their pending state at once (`pendingMs: 0`) would hold it for the
  // router's minimum, 500 ms of real time, before they commit. Tests have no flash to
  // avoid: without the minimum a load commits as soon as its data is in.
  router.update({ ...router.options, defaultPendingMinMs: 0 });
  await act(() => router.load());
  return renderRouter(router, queryClient);
}

/**
 * One component inside a bare router (a root route that renders it, on a memory
 * history), for components that hold `Link`s but do not need the app's route tree.
 */
export async function renderWithRouter(
  ui: ReactElement,
  {
    queryClient = createTestQueryClient(),
    viewport,
  }: { queryClient?: QueryClient; viewport?: Viewport } = {},
): Promise<RenderRouteResult> {
  if (viewport) setViewport(viewport);
  const router = createRouter({
    routeTree: createRootRoute({ component: () => ui }),
    history: createMemoryHistory({ initialEntries: ["/"] }),
  });
  await act(() => router.load());
  return renderRouter(router, queryClient);
}
