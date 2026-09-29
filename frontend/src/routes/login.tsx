// /login (P0-13's form, routed by P0-22): after the code, the session check is redone
// and the dashboard opens.
import { useQueryClient } from "@tanstack/react-query";
import { createFileRoute, useNavigate } from "@tanstack/react-router";

import { LoginPage } from "../components/auth/LoginPage";
import { sessionProbeOptions } from "../lib/session";

export const Route = createFileRoute("/login")({
  component: LoginRoute,
});

function LoginRoute() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return (
    <LoginPage
      onSignedIn={() => {
        queryClient.removeQueries({ queryKey: sessionProbeOptions().queryKey });
        void navigate({ to: "/", replace: true });
      }}
    />
  );
}
