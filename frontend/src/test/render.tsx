import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  render,
  type RenderOptions,
  type RenderResult,
} from "@testing-library/react";
import { userEvent, type UserEvent } from "@testing-library/user-event";
import type { ReactElement, ReactNode } from "react";

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
