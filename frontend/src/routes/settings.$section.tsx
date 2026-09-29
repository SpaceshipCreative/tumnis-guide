// Settings (P0-22 route; the sections arrive with P0-26).
import { createFileRoute, redirect } from "@tanstack/react-router";

import { Placeholder } from "../components/pages/Placeholder";

export const settingsSections = [
  "account",
  "sessions",
  "keys",
  "audit",
  "dead-letters",
  "workspace",
] as const;

export const Route = createFileRoute("/settings/$section")({
  beforeLoad: ({ params }) => {
    if (!(settingsSections as readonly string[]).includes(params.section)) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect
      throw redirect({
        to: "/settings/$section",
        params: { section: "account" },
        replace: true,
      });
    }
  },
  component: SettingsPage,
});

function SettingsPage() {
  const { section } = Route.useParams();
  return (
    <Placeholder title="Settings">
      <p className="text-muted">Section: {section}</p>
    </Placeholder>
  );
}
