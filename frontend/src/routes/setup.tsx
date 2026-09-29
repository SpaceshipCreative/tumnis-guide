// /setup (P0-13's first-run form, routed by P0-22).
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { SetupPage } from "../components/auth/SetupPage";
import { sessionProbeOptions } from "../lib/session";

export const Route = createFileRoute("/setup")({
  component: SetupRoute,
});

function SetupRoute() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (
    <SetupPage
      onDone={() => {
        queryClient.removeQueries({ queryKey: sessionProbeOptions().queryKey });
        void navigate({ to: "/", replace: true });
      }}
    />
  );
}
