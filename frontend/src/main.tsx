// The app (P0-22): the generated client configured once, one QueryClient, the router,
// the live socket and the service worker.
import "./styles.css";

import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { configureClient } from "./lib/client";
import { setUnauthorizedHandler } from "./lib/fetch";
import { registerServiceWorker } from "./lib/serviceWorker";
import { sessionProbeOptions } from "./lib/session";
import { connectLive } from "./lib/ws";
import { createQueryClient } from "./queryClient";
import { createAppRouter } from "./router";

configureClient();
const queryClient = createQueryClient();
const router = createAppRouter({ queryClient });
setUnauthorizedHandler(() => {
  queryClient.removeQueries({ queryKey: sessionProbeOptions().queryKey });
  if (router.state.location.pathname !== "/login") {
    void router.navigate({ to: "/login", replace: true });
  }
});
connectLive(queryClient);
registerServiceWorker();

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}
